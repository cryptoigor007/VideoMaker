# VideoMaker FIX | 2026.09.16-r54-json-sanitize | 2026-09-16
# CHANGED r54: sanitize control chars in Gemini JSON; fix JSON-retry (RuntimeError was not caught)
# PREV: r53 short-covers; r52 live-models; r51 packaging; r50 desc
"""Движок анализа — Gemini API."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)


# --- Gemini Key / Model manager ---
# 429 → сразу следующий ключ (без минутного backoff на том же ключе)
# 503 / таймаут сети → 1 короткий retry, затем СЛЕДУЮЩАЯ МОДЕЛЬ:
#   503 — это перегрузка модели на стороне Google, ротация ключей тут не помогает.
# Запоминаем последний успешный ключ и модель в ~/video_maker/cache/gemini_state.json

# Каталог моделей для GUI (порядок = рекомендуемый fallback).
# Выбор пользователя в настройках всегда первый в цепочке; остальные — только если выбранная недоступна (404/503).
AVAILABLE_GEMINI_MODELS: tuple[str, ...] = (
    "gemini-3.6-flash",        # текущий основной: качество
    "gemini-3.5-flash-lite",   # живучие lite — первыми в запасе (реже попадают под перегрузку)
    "gemini-3.1-flash-lite",
    "gemini-flash-latest",     # алиас актуальной стабильной flash
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
    "gemini-2.5-flash",        # у новых ключей может быть 404 — быстрый скип
)

# Fallback-цепочка = тот же каталог (без дублей). Не «прибита» к одной модели.

DEFAULT_MODEL_CHAIN: tuple[str, ...] = AVAILABLE_GEMINI_MODELS


def _redact_secret(text: str, secret: str) -> str:
    """Remove raw and URL-encoded API key values from exception/log text."""
    if not secret:
        return str(text)
    value = str(text)
    try:
        from urllib.parse import quote, quote_plus
        variants = {secret, quote(secret, safe=""), quote_plus(secret, safe="")}
    except Exception:
        variants = {secret}
    for variant in sorted((v for v in variants if v), key=len, reverse=True):
        value = value.replace(variant, "[REDACTED]")
    return value


_TRANSIENT_ERROR_MARKERS = (
    "503", "unavailable", "high demand", "overloaded", "temporarily",
    "500", "internal", "timed out", "timeout", "connect", "reset",
)


def _is_transient_gemini_error(exc: Exception) -> bool:
    """True для временных сбоев (503/500/таймаут/обрыв сети): виновата модель/сеть, не ключ."""
    name = type(exc).__name__.lower()
    if any(marker in name for marker in ("timeout", "connect", "remoteprotocol")):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_ERROR_MARKERS)


def list_available_gemini_models(
    api_keys: list[str] | None = None,
    api_key: str = "",
    log_fn=None,
) -> list[str]:
    """Актуальный список моделей Gemini с API (generateContent).

    По кнопке в GUI. При ошибке — fallback AVAILABLE_GEMINI_MODELS.
    """
    _log = log_fn or (lambda *a, **k: None)
    keys: list[str] = []
    if api_keys:
        for k in api_keys:
            k = (k or "").strip()
            if k and k not in keys:
                keys.append(k)
    k0 = (api_key or "").strip()
    if k0 and k0 not in keys:
        keys.insert(0, k0)
    if not keys:
        _log("[GEMINI] list models: нет API ключа → fallback-каталог")
        return list(AVAILABLE_GEMINI_MODELS)

    last_err = ""
    for key in keys:
        # --- SDK ---
        try:
            from google import genai

            client = genai.Client(api_key=key)
            names: list[str] = []
            for m in client.models.list():
                name = getattr(m, "name", None) or ""
                name = str(name).strip()
                if name.startswith("models/"):
                    name = name[len("models/") :]
                methods = (
                    getattr(m, "supported_generation_methods", None)
                    or getattr(m, "supported_actions", None)
                    or []
                )
                if methods:
                    ml = [str(x).lower() for x in methods]
                    if not any("generatecontent" in x or x == "generate" for x in ml):
                        continue
                low = name.lower()
                if name and ("gemini" in low or "gemma" in low):
                    if name not in names:
                        names.append(name)
            if names:
                _log(f"[GEMINI] list models OK (SDK): {len(names)}")
                return names
            last_err = "SDK empty"
        except Exception as e:
            # SDK exceptions occasionally embed request URLs or credentials.
            last_err = f"SDK: {_redact_secret(str(e), key)}"

        # --- REST ---
        try:
            import json as _json
            import urllib.error
            import urllib.request

            url = (
                "https://generativelanguage.googleapis.com/v1beta/models"
                f"?key={key}&pageSize=100"
            )
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=25) as resp:
                payload = _json.loads(resp.read().decode("utf-8", errors="replace"))
            names = []
            for m in payload.get("models") or []:
                name = str(m.get("name") or "").strip()
                if name.startswith("models/"):
                    name = name[len("models/") :]
                methods = m.get("supportedGenerationMethods") or []
                ml = [str(x).lower() for x in methods]
                if ml and "generatecontent" not in ml:
                    continue
                low = name.lower()
                if name and ("gemini" in low or "gemma" in low):
                    if name not in names:
                        names.append(name)
            if names:
                _log(f"[GEMINI] list models OK (REST): {len(names)}")
                return names
            last_err = "REST empty"
        except Exception as e:
            last_err = f"REST: {_redact_secret(str(e), key)}"
            _log(f"[GEMINI] list models: {last_err}")

    _log(f"[GEMINI] list models fallback ({last_err})")
    return list(AVAILABLE_GEMINI_MODELS)


STATE_PATH = Path.home() / "video_maker" / "cache" / "gemini_state.json"

# Stage D: response cache key = hash(audio) + model + schema
ANALYSIS_SCHEMA_VERSION = "v4-reject-empty-analysis"
ANALYSIS_CACHE_DIR = Path.home() / "video_maker" / "cache" / "gemini_analysis"


# --- r50: packaging text for YouTube copy-paste (description .txt only) ---
PLAYLIST_SEASON_URL = "https://www.youtube.com/playlist?list=PLOsH4yc61thU"
PLAYLIST_BLOCK_URL = "https://www.youtube.com/playlist?list=PLfOYg9HTzi0s"
PLAYLIST_BLOCK_LABEL = "Одиночество и отношения"
REQUIRED_HASHTAGS = ("#ТочкаНаблюдения", "#ТайныйКризисЧеловечества")


def _packaging_defaults(opts: dict | None = None) -> dict:
    """Normalize packaging options from GUI/settings (safe defaults)."""
    o = dict(opts or {})
    season = str(o.get("playlist_season_url") or PLAYLIST_SEASON_URL).strip()
    block = str(o.get("playlist_block_url") or PLAYLIST_BLOCK_URL).strip()
    label = str(o.get("playlist_block_label") or PLAYLIST_BLOCK_LABEL).strip()
    req = str(o.get("required_hashtags") or " ".join(REQUIRED_HASHTAGS)).strip()
    append = o.get("append_playlist_footer")
    if append is None:
        append = True
    max_len = o.get("shorts_desc_max_len")
    try:
        max_len = int(max_len) if max_len is not None else 280
    except (TypeError, ValueError):
        max_len = 280
    max_len = max(80, min(max_len, 500))
    return {
        "playlist_season_url": season,
        "playlist_block_url": block,
        "playlist_block_label": label or PLAYLIST_BLOCK_LABEL,
        "required_hashtags": req,
        "append_playlist_footer": bool(append),
        "prompt_extra": str(o.get("prompt_extra") or "").strip(),
        "prompt_body": str(o.get("prompt_body") or "").strip(),
        "shorts_desc_max_len": max_len,
        "shorts_use_template": bool(o.get("shorts_use_template", True)),
    }


def _build_playlist_footer_text(opts: dict | None = None) -> str:
    d = _packaging_defaults(opts)
    lines = []
    if d["playlist_season_url"]:
        lines.append(f"→ Весь сезон: {d['playlist_season_url']}")
    if d["playlist_block_url"]:
        lines.append(
            f"→ Блок «{d['playlist_block_label']}»: {d['playlist_block_url']}"
        )
    return "\n".join(lines)


def _normalize_hashtag_list(
    raw: str,
    *,
    required: bool = True,
    max_tags: int = 12,
    required_tags: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    tags: list[str] = []
    for part in (raw or "").replace(",", " ").split():
        p = part.strip()
        if not p:
            continue
        if not p.startswith("#"):
            p = "#" + p.lstrip("#")
        if not p.startswith("#") or len(p) < 2:
            continue
        if p not in tags:
            tags.append(p)
    if required:
        req_src = required_tags if required_tags is not None else REQUIRED_HASHTAGS
        ordered: list[str] = []
        for req in req_src:
            r = str(req).strip()
            if not r:
                continue
            if not r.startswith("#"):
                r = "#" + r.lstrip("#")
            if r not in ordered:
                ordered.append(r)
        for p in tags:
            if p not in ordered:
                ordered.append(p)
        tags = ordered
    return tags[:max_tags]



def _ensure_playlist_footer(
    description: str,
    hashtags: str = "",
    packaging_opts: dict | None = None,
) -> str:
    """Idempotent: body + playlist links + hashtags for full-video description.txt."""
    opts = _packaging_defaults(packaging_opts)
    body = (description or "").strip()
    harvested: list[str] = []
    lines = [ln.rstrip() for ln in body.splitlines()]
    cleaned: list[str] = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("→ Весь сезон:") or s.startswith("→ Блок"):
            continue
        if s.startswith("http") and "youtube.com/playlist" in s:
            continue
        if s and all(p.startswith("#") for p in s.split() if p):
            for p in s.split():
                if p.startswith("#") and p not in harvested:
                    harvested.append(p)
            continue
        cleaned.append(ln)
    body = "\n".join(cleaned).strip()
    req_raw = (opts.get("required_hashtags") or "").split()
    req_norm: list[str] = []
    for p in req_raw:
        p = p.strip()
        if not p:
            continue
        if not p.startswith("#"):
            p = "#" + p.lstrip("#")
        if p not in req_norm:
            req_norm.append(p)
    if not req_norm:
        req_norm = list(REQUIRED_HASHTAGS)
    merged_tags = " ".join(harvested + [(hashtags or "").strip()])
    tags = _normalize_hashtag_list(
        merged_tags, required=True, max_tags=12, required_tags=req_norm
    )
    tag_line = " ".join(tags)
    parts: list[str] = []
    if body:
        parts.append(body)
    if opts.get("append_playlist_footer", True):
        footer = _build_playlist_footer_text(opts)
        if footer:
            parts.append(footer)
    if tag_line:
        parts.append(tag_line)
    return "\n\n".join(parts).strip() + "\n"


def _format_short_description(
    description: str,
    hashtags: str = "",
    title: str = "",
    packaging_opts: dict | None = None,
) -> str:
    """Keep short description copy-paste friendly; do not force long series blurb."""
    desc = (description or "").strip()
    legacy = "Полная серия «Тайный кризис человечества» на канале «Точка наблюдения»."
    if legacy in desc:
        desc = desc.replace(legacy, "").strip()
    for tail in (
        "Напишите в комментариях, согласны ли вы.",
        "Напишите в комментариях согласны ли вы.",
    ):
        if desc.endswith(tail):
            desc = desc[: -len(tail)].strip()
    harvested: list[str] = []
    body_lines: list[str] = []
    for ln in desc.splitlines():
        s = ln.strip()
        if s and all(p.startswith("#") for p in s.split() if p):
            for p in s.split():
                if p.startswith("#") and p not in harvested:
                    harvested.append(p)
            continue
        body_lines.append(ln)
    body = "\n".join(body_lines).strip()
    if not body and title:
        body = f"{title.strip()} 💡"
    opts = _packaging_defaults(packaging_opts)
    req_raw = (opts.get("required_hashtags") or "").split()
    req_norm = []
    for p in req_raw:
        p = str(p).strip()
        if not p:
            continue
        if not p.startswith("#"):
            p = "#" + p.lstrip("#")
        if p not in req_norm:
            req_norm.append(p)
    tags = _normalize_hashtag_list(
        " ".join(harvested + [(hashtags or "").strip()]),
        required=True,
        max_tags=5,
        required_tags=req_norm or None,
    )
    tag_line = " ".join(tags)
    if tag_line and tag_line not in body:
        out = (body + "\n" + tag_line).strip() if body else tag_line
    else:
        out = body
    max_len = int(_packaging_defaults(packaging_opts).get("shorts_desc_max_len", 280))
    if len(out) > max_len:
        out = out[: max(0, max_len - 1)].rstrip() + "…"
    return out.strip() + ("\n" if out else "")


def _audio_fingerprint(audio_path: str) -> str:
    """Стабильный fingerprint аудио: path + size + mtime (быстро, без полного hash файла)."""
    if not audio_path or not os.path.isfile(audio_path):
        return "noaudio"
    try:
        st = os.stat(audio_path)
        raw = f"{os.path.abspath(audio_path)}|{st.st_size}|{st.st_mtime_ns}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]
    except OSError:
        return "noaudio"


def _analysis_cache_key(
    audio_path: str,
    model_name: str,
    intro_gemini: bool,
    series_name: str,
    transcription: dict | None,
    packaging_opts: dict | None = None,
) -> str:
    """Gemini cache key = hash(audio) + model + schema (+ intro/series + text + packaging)."""
    audio_fp = _audio_fingerprint(audio_path)
    # Include the complete transcript in the cache identity. Using only the first
    # 50 segments allowed two transcripts that diverged later to share a Gemini result.
    text_digest = hashlib.sha256()
    if transcription:
        for segment in transcription.get("segments") or []:
            text = str(segment.get("text") or "") if isinstance(segment, dict) else ""
            encoded = text.encode("utf-8", errors="ignore")
            text_digest.update(len(encoded).to_bytes(8, "big"))
            text_digest.update(encoded)
    text_fp = text_digest.hexdigest()[:16]
    pkg = _packaging_defaults(packaging_opts)
    pkg_fp = hashlib.sha256(
        repr(sorted(pkg.items())).encode("utf-8", errors="ignore")
    ).hexdigest()[:12]
    raw = (
        f"{audio_fp}|{model_name}|{ANALYSIS_SCHEMA_VERSION}|"
        f"intro={int(bool(intro_gemini))}|series={series_name or ''}|txt={text_fp}|pkg={pkg_fp}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def _analysis_cache_get(key: str, log_fn=None) -> dict | None:
    _log = log_fn or log.info
    try:
        ANALYSIS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = ANALYSIS_CACHE_DIR / f"{key}.json"
        if not path.is_file():
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data:
            return None
        _log(f"[GEMINI] cache HIT key={key[:12]}…")
        return data
    except Exception as e:
        _log(f"[GEMINI] cache read fail: {e}")
        return None


def _analysis_cache_put(key: str, analysis: dict, log_fn=None) -> None:
    _log = log_fn or log.info
    try:
        ANALYSIS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = ANALYSIS_CACHE_DIR / f"{key}.json"
        tmp = f"{path}.{os.getpid()}.{time.time_ns()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(analysis, f, ensure_ascii=False, indent=0)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _log(f"[GEMINI] cache STORE key={key[:12]}…")
    except Exception as e:
        _log(f"[GEMINI] cache write fail: {e}")


def _parse_keys(*sources) -> list[str]:
    """Ключи: список или строка (запятая / перевод строки / ;). Без дублей, порядок сохраняем."""
    out: list[str] = []
    seen: set[str] = set()
    for src in sources:
        if not src:
            continue
        if isinstance(src, (list, tuple)):
            parts = list(src)
        else:
            parts = re.split(r"[\n,;]+", str(src))
        for p in parts:
            k = p.strip()
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(k)
    return out


def _load_state() -> dict:
    try:
        if STATE_PATH.is_file():
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


class GeminiKeyManager:
    """Ротация ключей + выбор модели. Не держит пайплайн на 120с backoff."""

    def __init__(self, keys: list[str], preferred_model: str = "", log_fn=None):
        self.keys = keys
        self.log = log_fn or log.info
        self.n = len(keys)
        state = _load_state()
        # стартуем с последнего успешного ключа
        start = int(state.get("last_key_index", 0) or 0)
        if start < 0 or start >= self.n:
            start = 0
        self.key_index = start
        self.exhausted: set[int] = set()  # индексы с 429 / invalid в этой сессии
        self.model = (preferred_model or "").strip() or state.get("last_model") or DEFAULT_MODEL_CHAIN[0]
        self.model_chain = self._build_model_chain(self.model)
        self.model_index = 0
        self.log(
            f"[GEMINI] Ключей: {self.n} | старт с ключа {self.key_index+1}/{self.n} | "
            f"модель: {self.model_chain[0]}"
        )

    def _build_model_chain(self, preferred: str) -> list[str]:
        """Выбранная в настройках модель — первая; дальше каталог как fallback при 404."""
        chain: list[str] = []
        pref = (preferred or "").strip()
        if pref:
            chain.append(pref)
        for m in AVAILABLE_GEMINI_MODELS:
            if m and m not in chain:
                chain.append(m)
        return chain or ["gemini-3.6-flash"]

    @property
    def current_key(self) -> str:
        return self.keys[self.key_index]

    @property
    def current_model(self) -> str:
        return self.model_chain[self.model_index]

    def mark_success(self) -> None:
        _save_state({
            "last_key_index": self.key_index,
            "last_model": self.current_model,
        })

    def rotate_key(self, reason: str) -> bool:
        """Пометить текущий ключ и перейти к следующему живому. False = ключи кончились."""
        self.exhausted.add(self.key_index)
        self.log(
            f"[GEMINI] {reason} → ключ {self.key_index+1}/{self.n} пропускаем "
            f"(exhausted={len(self.exhausted)}/{self.n})"
        )
        for _ in range(self.n):
            self.key_index = (self.key_index + 1) % self.n
            if self.key_index not in self.exhausted:
                self.log(f"[GEMINI] Переключение на ключ {self.key_index+1}/{self.n}")
                return True
        return False

    def next_model(self) -> bool:
        if self.model_index + 1 < len(self.model_chain):
            self.model_index += 1
            self.log(f"[GEMINI] Модель → {self.current_model}")
            # при смене модели можно снова пробовать ключи (кроме invalid — но invalid редкий)
            return True
        return False



def _build_analysis_prompt(text: str, segments: list[dict], intro_gemini: bool = True, series_name: str = "", packaging_opts: dict | None = None) -> str:
    """Промпт Gemini: packaging полного видео + Shorts-стратег (Точка наблюдения)."""
    opts = _packaging_defaults(packaging_opts)
    timings = "\n".join(
        f"[{s.get('start', 0):.1f}-{s.get('end', 0):.1f}] {s.get('text', '')}"
        for s in (segments or [])[:400]
    )
    series_context = f"\nНазвание серии (если задано): «{series_name}»." if series_name else ""
    intro_section = ""
    if intro_gemini:
        intro_section = """
═══════════════════════════════════════
6) INTRO / MIDDLE / OUTRO (для склейки)
═══════════════════════════════════════
- intro: {start, end} — первые 2–5с сильного входа
- middle: список {start,end} точек разворота (0–3 шт)
- outro: {start, end} — финальные 2–5с
"""

    return f"""Ты — опытный контент-стратег YouTube Shorts и senior packaging-editor.
Канал: «Точка наблюдения». Сериал: «Тайный кризис человечества».{series_context}

Ты знаешь метрики алгоритма YouTube (average view duration, average % viewed, swipe-away rate,
replay rate, likes/comments/shares per view, CTR обложки).

ЗАДАЧА: разобрать текст/таймкоды на:
1) packaging полного ролика (wide+vertical),
2) on-screen хуки/CTA,
3) 4–5 Shorts-клипов максимального потенциала.

═══════════════════════════════════════
ШАГ 0. ХАРАКТЕРИСТИКИ ВХОДА (внутренне)
═══════════════════════════════════════
- Таймкоды: ниже дан список [start-end] текст — используй ИХ точно. Не выдумывай секунды.
- Формат речи: монолог или диалог — определи сам.
- Целевое число Shorts: 4–5 сильных клипов на весь текст (не «каждые 2–3 минуты»).
  Если сильных меньше — честно меньше, не растягивай.
- Ниша: из содержания текста (для нишевых хэштегов и тона).
- Канал/сериал фиксированы: «Точка наблюдения» / «Тайный кризис человечества».

═══════════════════════════════════════
ЖЁСТКИЕ ПРАВИЛА ON-SCREEN ТЕКСТА
═══════════════════════════════════════
- ХУК и CTA — PACKAGING, не субтитры. ЗАПРЕЩЕНО копировать дословно фразу из транскрипта.
- Переписывай: короче, острее, интрига / конфликт / вопрос / ставка.
- Хук ≠ то, что говорит голос. Хук — «заголовок кадра» в первые 0–3с.
- CTA ≠ финальная фраза речи. CTA — отдельный призыв написать комментарий.
- CTA только в конце (~последние 5с). Хук не ставить в зону CTA.

═══════════════════════════════════════
1) HOOKS ПОЛНОГО ВИДЕО (on-screen)
═══════════════════════════════════════
A) hooks_wide (16:9) — РОВНО 1 шт: умный/сериальный тон, 3–7 слов, start≈0, показ 2–3.5с
B) hooks_vertical (9:16) — РОВНО 1 шт: сильнее pattern-interrupt, 3–6 слов, ДРУГОЙ текст, чем wide
Legacy "hooks" = hooks_vertical.
type: QUESTION | CONTRADICTION | STATEMENT | CURIOSITY | IDENTITY | LOSS | REVELATION
Хук — только в начале ролика. Больше одного хука на long-видео ЗАПРЕЩЕНО.

═══════════════════════════════════════
2) CTA ПОЛНОГО ВИДЕО (on-screen, конец)
═══════════════════════════════════════
- cta_wide: 3–8 слов — вопрос/спор для комментариев YouTube
- cta_vertical: 3–7 слов — другой текст для вертикали
- НЕ повторять последнюю фразу транскрипта
- start/end можно 0 — пайплайн выставит конец ролика

═══════════════════════════════════════
3) PACKAGING ПОЛНОГО ВИДЕО (package_* — одинаково для wide и vertical)
═══════════════════════════════════════
- package_title ≤ 70 символов
- package_description: готовый текст для копирования в YouTube (красиво, по абзацам):
  1) 2–4 предложения: тема выпуска + интрига (сериал «Тайный кризис человечества», канал «Точка наблюдения»)
  2) блок «📍 В этом видео:» — 4–6 пунктов с таймкодами из источника
  3) вопрос в комментарии + призыв подписаться
  4) НЕ вставляй ссылки на плейлисты и НЕ дублируй длинный список хештегов в конце —
     пайплайн сам добавит фиксированный блок ссылок и хештеги в description.txt
- package_hook: 3–8 слов — главный packaging-хук
- package_hashtags: 8–12 через пробел, ОБЯЗАТЕЛЬНО #ТочкаНаблюдения #ТайныйКризисЧеловечества

═══════════════════════════════════════
4) SHORTS (отдельный продукт, 4–5 клипов)
═══════════════════════════════════════
Критерии клипа:
- самодостаточность (сетап + пик);
- хук 0–3с (packaging-фраза, не дословная речь);
- один смысловой пик; 15–60 сек;
- чистые границы мысли; яркая финальная фраза;
- эмоция/польза; потенциал комментариев.
Если текст с отсылкой «в следующем видео» — для последнего клипа вариант А (обрезать) или Б (клиффхэнгер).

У КАЖДОГО Shorts СВОИ поля (не копировать package_*):
- text: ПОЛНЫЙ дословный текст фрагмента без «…»
- start / end: ТОЛЬКО из таймкодов источника (абсолютные секунды всего ролика)
- hook: 3–6 слов packaging для первого кадра
- hook_start / hook_end: АБСОЛЮТНЫЕ секунды на таймлайне полного ролика
  (hook_start ≈ start клипа, длительность показа ~2–3с)
- cta: 3–7 слов — вопрос в комментарии (≠ hook)
- cta_start / cta_end: АБСОЛЮТНЫЕ, последние ~5с клипа (cta_end ≈ end)
- title ≤ 40 символов
- description: ГОТОВЫЙ текст для Shorts (copy-paste), лаконично, целевой объём до ~200 символов:
  1) [Крючок]: первое предложение с проблемой/вопросом и эмодзи (суть клипа)
  2) [Контекст/Интрига]: 1–2 коротких предложения — доп. инсайт, желание досмотреть
  3) [CTA Комментарии]: вопрос с 2 вариантами ответа (провокация обсуждения)
  4) [CTA Действие]: «📌 Сохрани и отправь другу! 🔔 Подпишись для [краткая польза].»
  5) Хештеги можно не писать в description — они будут в поле hashtags (3–5 штук)
  Без воды, без длинных отсылок «смотрите полную серию…»
- hashtags: 3–5 через пробел; ОБЯЗАТЕЛЬНО #ТочкаНаблюдения #ТайныйКризисЧеловечества + 1–3 нишевых
- scores (опционально): hook/self/emotion/comments/reposts 1–10, total

Внутренне доводи каждый клип до ≥8/10 (ни один критерий не ниже 6). Слабые отбрасывай.
Не завышай баллы. Не выдумывай текст и таймкоды.

═══════════════════════════════════════
5) СИЛЬНЫЕ СЛОВА + СУБТИТРЫ
═══════════════════════════════════════
- strong_words: 5–15 слов с timing/start/end, caps, color, visual_weight
- subtitles: смысловые куски 1.5–4с (fallback)
{intro_section}
Текст с таймингами:
{timings}

Верни ТОЛЬКО валидный JSON (без markdown, без пояснений):
{{
  "corrected_text": "полный исправленный текст с пунктуацией",
  "hooks_wide": [
    {{"text": "ПЕРЕПИСАННЫЙ хук для 16:9", "start": 0.0, "end": 2.5, "type": "CURIOSITY", "visual_weight": "L3"}}
  ],
  "hooks_vertical": [
    {{"text": "ДРУГОЙ переписанный хук для 9:16", "start": 0.0, "end": 2.5, "type": "CURIOSITY", "visual_weight": "L3"}}
  ],
  "hooks": [
    {{"text": "fallback = hooks_vertical", "start": 0.0, "end": 2.5, "type": "CURIOSITY", "visual_weight": "L3"}}
  ],
  "hook": {{"text": "главный хук", "start": 0.0, "end": 2.5, "timing": 0.0}},
  "cta_wide": {{"text": "Вопрос для комментариев YouTube", "start": 0.0, "end": 0.0}},
  "cta_vertical": {{"text": "Другой вопрос для вертикали", "start": 0.0, "end": 0.0}},
  "package_title": "Заголовок полного ролика ≤70",
  "package_description": "Текст выпуска + таймкоды + вопрос (ссылки/хештеги добавит пайплайн)",
  "package_hook": "Главный packaging-хук 3–8 слов",
  "package_hashtags": "#ТочкаНаблюдения #ТайныйКризисЧеловечества #tag3 #tag4 #tag5 #tag6 #tag7 #tag8",
  "clips_for_shorts": [
    {{
      "text": "полный текст этого Short",
      "start": 0.0,
      "end": 18.0,
      "hook": "PACKAGING-хук обложки",
      "hook_start": 0.0,
      "hook_end": 2.5,
      "cta": "Вопрос — напиши в комментариях",
      "cta_start": 13.0,
      "cta_end": 18.0,
      "title": "заголовок ≤40",
      "description": "Крючок с эмодзи. Интрига. Вопрос: А или Б?\n📌 Сохрани! 🔔 Подпишись для разборов.",
      "hashtags": "#ТочкаНаблюдения #ТайныйКризисЧеловечества #одиночество"
    }}
  ],
  "intro": {{"start": 0.0, "end": 3.5}},
  "middle": [{{"start": 10.0, "end": 14.0}}],
  "outro": {{"start": 25.0, "end": 30.0}},
  "strong_words": [
    {{"word": "слово", "timing": 1.2, "start": 1.2, "end": 2.4, "caps": true, "color": "#FF6B00", "visual_weight": "L2"}}
  ],
  "subtitles": [
    {{"start": 0.0, "end": 2.5, "text": "фраза субтитра", "style": "normal"}}
  ]
}}"""



def get_builtin_analysis_prompt(
    series_name: str = "",
    intro_gemini: bool = True,
) -> str:
    """Встроенный промпт для GUI (редактор / сброс)."""
    demo_segments = [
        {"start": 0.0, "end": 2.0, "text": "…фрагмент транскрипта…"},
    ]
    return _build_analysis_prompt(
        "…полный текст будет подставлен из транскрипции…",
        demo_segments,
        intro_gemini=intro_gemini,
        series_name=series_name or "",
        packaging_opts=None,
    )


def analyze(
    transcription: dict,
    api_key: str = "",
    api_keys: list[str] | None = None,
    model_name: str = "gemini-3.6-flash",
    intro_gemini: bool = True,
    series_name: str = "",
    log_fn=None,
    audio_path: str = "",
    packaging_opts: dict | None = None,
) -> dict:
    """Единый вызов Gemini → пакет ANALYSIS. Нормальная ротация ключей/моделей.

    Stage D: cache key = hash(audio) + model + schema.
    """
    _log = log_fn or log.info

    keys = _parse_keys(api_keys, api_key)
    if not keys:
        _log("[GEMINI] API ключ не задан, пропускаем")
        return _empty_analysis()

    segments = transcription.get("segments", []) if transcription else []
    if not segments:
        _log("[GEMINI] Нет сегментов для анализа")
        return _empty_analysis()

    # Stage D — response cache
    pkg = _packaging_defaults(packaging_opts)
    cache_key = _analysis_cache_key(
        audio_path or "",
        model_name or "gemini-3.6-flash",
        bool(intro_gemini),
        series_name or "",
        transcription,
        packaging_opts=pkg,
    )
    cached = _analysis_cache_get(cache_key, log_fn=_log)
    if cached is not None:
        return cached

    full_text = " ".join(s.get("text", "") for s in segments)
    # Полный промпт: GUI prompt_body или встроенный
    custom_body = (pkg.get("prompt_body") or "").strip()
    if custom_body:
        prompt = custom_body
        timings = "\n".join(
            f"[{s.get('start', 0):.1f}-{s.get('end', 0):.1f}] {s.get('text', '')}"
            for s in (segments or [])[:400]
        )
        if "ТАЙМКОДЫ_ТРАНСКРИПТА" in prompt:
            prompt = prompt.replace("ТАЙМКОДЫ_ТРАНСКРИПТА", timings)
            prompt = prompt.replace("ПОЛНЫЙ_ТЕКСТ_ТРАНСКРИПТА", full_text)
        elif timings and timings[:60] not in prompt:
            prompt = (
                prompt
                + "\n\n═══════════════════════════════════════\n"
                + "ТАЙМКОДЫ (источник, не выдумывай секунды)\n"
                + "═══════════════════════════════════════\n"
                + timings
                + "\n\nПОЛНЫЙ ТЕКСТ:\n"
                + full_text
            )
    else:
        prompt = _build_analysis_prompt(
            full_text, segments, intro_gemini, series_name, packaging_opts=pkg
        )
    if not pkg.get("shorts_use_template", True):
        prompt = (
            prompt
            + "\n\n[SHORTS description] Свободный формат 1–3 предложения + CTA; лимит ~"
            + str(pkg.get("shorts_desc_max_len", 280))
            + " символов.\n"
        )
    if pkg.get("prompt_extra"):
        prompt = (
            prompt
            + "\n\n═══════════════════════════════════════\n"
            + "ДОП. ИНСТРУКЦИИ ПОЛЬЗОВАТЕЛЯ\n"
            + "═══════════════════════════════════════\n"
            + pkg["prompt_extra"]
        )

    km = GeminiKeyManager(keys, preferred_model=model_name, log_fn=_log)
    _log(f"[GEMINI] Анализ | ключей={km.n} | модель={km.current_model} | cache_key={cache_key[:12]}…")

    # лимиты: на одну МОДЕЛЬ для 503/таймаута — не больше 1 короткого retry
    max_transient_per_model = 1
    consecutive_transient = 0
    json_retries = 0
    # общий потолок попыток — без бесконечного цикла (модели × retry-запасы + ключи)
    max_attempts = max(len(km.model_chain) * 5 + km.n * 3, 8)
    attempt = 0

    while attempt < max_attempts:
        attempt += 1
        key = km.current_key
        model = km.current_model
        try:
            from google import genai
            client = genai.Client(api_key=key)
            _log(f"[GEMINI] Запрос model={model} key={km.key_index+1}/{km.n} try={attempt}")
            response = client.models.generate_content(model=model, contents=prompt)
            raw = response.text
            if not raw or not raw.strip():
                raise RuntimeError("Gemini вернул пустой ответ")
            analysis = _parse_analysis(raw, segments, packaging_opts=pkg)
            if _is_weak_analysis(analysis):
                preview = (raw or "").strip().replace("\n", " ")[:220]
                raise RuntimeError(
                    "Gemini вернул пустой ANALYSIS (shorts=0, title='', hooks=0). "
                    f"preview={preview!r}"
                )
            km.mark_success()
            _log(
                f"[GEMINI] OK: shorts={len(analysis.get('clips_for_shorts', []))} "
                f"hooks_w={len(analysis.get('hooks_wide') or [])} "
                f"hooks_v={len(analysis.get('hooks_vertical') or [])} "
                f"package_title={str(analysis.get('package_title') or '')[:50]!r}"
            )
            _analysis_cache_put(cache_key, analysis, log_fn=_log)
            return analysis

        except Exception as e:
            err_str = str(e)
            _log(f"[GEMINI] Ошибка API: {err_str[:400]}")

            # --- 429 / quota: сразу следующий ключ, без sleep ---
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                consecutive_transient = 0
                if not km.rotate_key("429 quota"):
                    if km.next_model():
                        km.exhausted.clear()  # новая модель — новые квоты
                        continue
                    raise RuntimeError(
                        "Gemini: квота исчерпана на всех ключах/моделях. "
                        "Добавьте ключи или подождите сброса лимита."
                    ) from e
                time.sleep(0.5)
                continue

            # --- 503 / UNAVAILABLE / таймаут сети: 1 короткий retry, затем следующая МОДЕЛЬ ---
            # 503 у Google — свойство модели (все ключи получают одно и то же), поэтому
            # ключи не ротируем: ротация только жгла ключи и растягивала прогон на минуты.
            if _is_transient_gemini_error(e):
                consecutive_transient += 1
                if consecutive_transient <= max_transient_per_model:
                    wait = 2.0 * consecutive_transient  # 2с — НЕ 10/20/40/80/120
                    _log(
                        f"[GEMINI] Перегрузка/сбой сети — короткий retry "
                        f"{consecutive_transient}/{max_transient_per_model} через {wait:.0f}с"
                    )
                    time.sleep(wait)
                    continue
                consecutive_transient = 0
                if km.next_model():
                    km.exhausted.clear()
                    continue
                raise RuntimeError(
                    "Gemini временно недоступен (перегрузка 503 / сеть) на всех моделях. "
                    "Попробуйте позже."
                ) from e

            # --- invalid key ---
            if "API_KEY_INVALID" in err_str or "API key not valid" in err_str:
                consecutive_transient = 0
                if not km.rotate_key("invalid key"):
                    raise RuntimeError(
                        "Недействительный Gemini API ключ (все ключи)."
                    ) from e
                continue

            # --- model not found → следующая модель ---
            if "NOT_FOUND" in err_str and "model" in err_str.lower():
                consecutive_transient = 0
                _log(f"[GEMINI] Модель {model} недоступна")
                if km.next_model():
                    continue
                raise RuntimeError(
                    f"Модель Gemini недоступна: {model_name} и fallback-цепочка."
                ) from e

            # --- JSON parse (RuntimeError из _parse_analysis или сырой JSONDecodeError) ---
            is_json_err = (
                isinstance(e, (json.JSONDecodeError, ValueError))
                or "JSON parse error" in err_str
                or "JSONDecodeError" in err_str
            )
            if is_json_err and "JSON" in err_str:
                if json_retries < 2:  # было 1 → даём 2 попытки + sanitize уже внутри parse
                    json_retries += 1
                    _log(f"[GEMINI] JSON parse error — повтор со строгим промптом ({json_retries}/2)")
                    prompt += (
                        "\n\nВАЖНО: Верни ТОЛЬКО валидный JSON без markdown, "
                        "без комментариев, без лишнего текста. "
                        "Никаких управляющих символов внутри строк."
                    )
                    time.sleep(1.0)
                    continue
                # после sanitize + retries — пробуем следующую модель
                consecutive_transient = 0
                if km.next_model():
                    json_retries = 0
                    _log(f"[GEMINI] JSON всё ещё битый → модель {km.current_model}")
                    continue
                raise

            _log(f"[GEMINI] Ошибка: {e}")
            raise

    raise RuntimeError("Gemini: превышено число попыток (ключи/модели)")




def _is_weak_analysis(analysis: dict | None) -> bool:
    """True если нет ни shorts, ни title, ни hooks — бесполезный ответ."""
    if not analysis or not isinstance(analysis, dict):
        return True
    clips = analysis.get("clips_for_shorts") or []
    title = str(analysis.get("package_title") or "").strip()
    hooks = (
        analysis.get("hooks_vertical")
        or analysis.get("hooks_wide")
        or analysis.get("hooks")
        or []
    )
    return (not clips) and (not title) and (not hooks)


def _sanitize_json_control_chars(text: str) -> str:
    """Убрать control characters, из-за которых json.loads падает.

    Gemini иногда вставляет сырые \\x00-\\x1f (включая \\t/\\n/\\r) *внутри* строк —
    это невалидный JSON (допустимы только escaped). Все C0 → пробел (безопасно
    и для whitespace вне строк, и для мусора внутри).
    """
    if not text:
        return text
    return re.sub(r"[\x00-\x1f]", " ", text)


def _parse_analysis(raw: str, segments: list[dict], packaging_opts: dict | None = None) -> dict:
    """Распарсить ответ Gemini. Битый JSON → exception (ротация модели)."""
    text = (raw or "").strip()
    if not text:
        raise RuntimeError("Gemini вернул пустой текст")
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()
    # иногда модель оборачивает JSON в пояснения — вырежем первый {...}
    if not text.startswith("{"):
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            text = text[a : b + 1]
    # 1) попытка as-is
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 2) sanitize control chars и повтор
        cleaned = _sanitize_json_control_chars(text)
        try:
            data = json.loads(cleaned)
            log.warning("[GEMINI] JSON принят после очистки control characters")
        except json.JSONDecodeError as e:
            log.error("[GEMINI] Ошибка парсинга JSON: %s. Ответ: %s", e, (raw or "")[:500])
            raise RuntimeError(f"Gemini JSON parse error: {e}") from e
    if not isinstance(data, dict):
        raise RuntimeError("Gemini JSON не объект")
    return _normalize_analysis(data, segments, packaging_opts=packaging_opts)



def _normalize_analysis(data: dict, segments: list[dict], packaging_opts: dict | None = None) -> dict:
    """Нормализовать ANALYSIS пакет с fallback на Whisper segments для субтитров."""
    corrected_text = data.get("corrected_text", "")
    
    # Fallback: если нет corrected_text — собираем из Whisper segments
    if not corrected_text:
        corrected_text = " ".join(s.get("text", "") for s in segments)

    # Subtitles: если Gemini не вернул — строим из Whisper segments
    subtitles = data.get("subtitles", [])
    if not subtitles:
        subtitles = []
        for s in segments:
            start = s.get("start", 0)
            end = s.get("end", 0)
            text = s.get("text", "").strip()
            if text:
                subtitles.append({
                    "start": start,
                    "end": end,
                    "text": text,
                    "style": "normal"
                })

    def _norm_hook_list(raw_list):
        out = []
        for h in raw_list or []:
            if not isinstance(h, dict) or not h.get("text"):
                continue
            start = float(h.get("start", h.get("timing", 0)) or 0)
            end = float(h.get("end", start + 2.5) or (start + 2.5))
            out.append({
                "text": str(h.get("text", "")).strip(),
                "start": start,
                "end": end,
                "timing": start,
                "type": h.get("type", "STATEMENT"),
                "visual_weight": h.get("visual_weight", "L3"),
            })
        return out

    hooks_vertical = _norm_hook_list(data.get("hooks_vertical") or data.get("hooks") or [])
    hooks_wide = _norm_hook_list(data.get("hooks_wide") or [])
    # Long video: keep at most 1 hook per orientation (shorts have their own per-clip hooks)
    if len(hooks_vertical) > 1:
        hooks_vertical = hooks_vertical[:1]
    if len(hooks_wide) > 1:
        hooks_wide = hooks_wide[:1]
    if not hooks_wide:
        hooks_wide = list(hooks_vertical)
    hooks = hooks_vertical  # legacy default = vertical

    if not hooks:
        h = data.get("hook") or {}
        if isinstance(h, dict) and h.get("text"):
            start = float(h.get("start", h.get("timing", 0)) or 0)
            end = float(h.get("end", start + 2.5) or (start + 2.5))
            hooks = [{
                "text": str(h.get("text", "")).strip(),
                "start": start,
                "end": end,
                "timing": start,
                "type": h.get("type", "STATEMENT"),
                "visual_weight": h.get("visual_weight", "L3"),
            }]
            hooks_vertical = hooks
            if not hooks_wide:
                hooks_wide = list(hooks)

    main_hook = data.get("hook") or (hooks[0] if hooks else {"text": "", "timing": 0, "start": 0, "end": 0})
    if isinstance(main_hook, dict) and "start" not in main_hook:
        t = float(main_hook.get("timing", 0) or 0)
        main_hook = {**main_hook, "start": t, "end": float(main_hook.get("end", t + 2.5))}

    def _norm_cta(raw, default_start=0.0, default_end=0.0):
        if isinstance(raw, dict) and raw.get("text"):
            s = float(raw.get("start", default_start) or default_start)
            e = float(raw.get("end", default_end or (s + 2.5)) or (s + 2.5))
            return {"text": str(raw.get("text")).strip(), "start": s, "end": e}
        if isinstance(raw, str) and raw.strip():
            return {"text": raw.strip(), "start": default_start, "end": default_end or (default_start + 2.5)}
        return None

    cta_wide = _norm_cta(data.get("cta_wide") or data.get("cta"))
    cta_vertical = _norm_cta(data.get("cta_vertical") or data.get("cta"))

    # Shorts: hook + CTA на клип (абсолютные таймкоды на таймлайне полного ролика)
    # required tags from GUI packaging_opts (fallback to brand defaults)
    _pkg = _packaging_defaults(packaging_opts)
    _req_tags = [
        t if t.startswith("#") else f"#{t}"
        for t in (_pkg.get("required_hashtags") or "").split()
        if t.strip()
    ] or list(REQUIRED_HASHTAGS)

    def _abs_time(val, fallback, clip_start):
        """Если модель вернула относительное время (< начала клипа) — сдвигаем."""
        try:
            t = float(val)
        except (TypeError, ValueError):
            t = float(fallback)
        # 0.0 or() уже обработан снаружи; относительные 0..dur
        if t < clip_start - 0.05:
            t = clip_start + max(0.0, t)
        return t

    clips = []
    for c in data.get("clips_for_shorts") or []:
        if not isinstance(c, dict):
            continue
        c_start = float(c.get("start", 0) or 0)
        c_end = float(c.get("end", c_start + 15) or (c_start + 15))
        if c_end <= c_start:
            c_end = c_start + 15.0
        # hook times
        raw_hs = c.get("hook_start", c_start)
        if raw_hs is None or (isinstance(raw_hs, (int, float)) and float(raw_hs) == 0.0 and c_start > 0.5):
            # 0 при ненулевом старте клипа → начало клипа
            hs = c_start
        else:
            hs = _abs_time(raw_hs, c_start, c_start)
        he = _abs_time(c.get("hook_end", hs + 2.5), hs + 2.5, c_start)
        if he <= hs:
            he = hs + 2.5
        # не залезать в CTA-зону
        he = min(he, max(hs + 1.0, c_end - 5.0))

        cta_text = (c.get("cta") or "").strip() if isinstance(c.get("cta"), str) else (
            (c.get("cta") or {}).get("text", "") if isinstance(c.get("cta"), dict) else ""
        )
        cs = _abs_time(c.get("cta_start"), max(c_start, c_end - 5.0), c_start)
        ce = _abs_time(c.get("cta_end"), c_end, c_start)
        if ce <= cs:
            cs = max(c_start, c_end - 5.0)
            ce = c_end
        # CTA строго в конце
        if cs < c_end - 8.0:
            cs = max(c_start, c_end - 5.0)
            ce = c_end

        # r50: short description — template from Gemini; no long series blurb
        desc = _format_short_description(
            str(c.get("description") or ""),
            hashtags=str(c.get("hashtags") or ""),
            title=str(c.get("title") or ""),
            packaging_opts=packaging_opts,
        )
        ht = " ".join(
            _normalize_hashtag_list(
                str(c.get("hashtags") or ""),
                required=True,
                max_tags=5,
                required_tags=_req_tags,
            )
        )

        item = {
            **c,
            "start": c_start,
            "end": c_end,
            "hook": str(c.get("hook", "") or "").strip(),
            "hook_start": hs,
            "hook_end": he,
            "cta": cta_text,
            "cta_start": cs,
            "cta_end": ce,
            "title": str(c.get("title") or "").strip()[:40],
            "description": desc.strip(),
            "hashtags": ht,
        }
        clips.append(item)

    def _norm_middle(raw):
        """Валидировать middle: только словари с числовым start.

        Невалидные элементы (строки, числа, текстовые тайминги) раньше падали
        в float() уже во время рендера — отбрасываем их здесь.
        """
        out = []
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            try:
                start = float(item.get("start"))
            except (TypeError, ValueError):
                continue
            entry = dict(item)
            entry["start"] = start
            try:
                entry["end"] = float(item.get("end"))
            except (TypeError, ValueError):
                entry.pop("end", None)
            out.append(entry)
        return out

    def _str_field(*keys, default=""):
        for k in keys:
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, dict) and (v.get("text") or "").strip():
                return str(v.get("text")).strip()
        return default

    # Packaging полного видео (wide + vertical — один набор)
    package_title = _str_field("package_title", "title")
    package_description = _str_field("package_description", "description")
    package_hook = _str_field("package_hook")
    if not package_hook:
        if isinstance(main_hook, dict):
            package_hook = str(main_hook.get("text") or "").strip()
        elif isinstance(main_hook, str):
            package_hook = main_hook.strip()
    package_hashtags = _str_field("package_hashtags", "hashtags")
    # обязательные теги из packaging_opts (GUI), не хардкод
    package_hashtags = " ".join(
        _normalize_hashtag_list(
            package_hashtags,
            required=True,
            max_tags=12,
            required_tags=_req_tags,
        )
    )

    # r50: full-video description.txt — body + playlists + hashtags (copy-paste)
    package_description = _ensure_playlist_footer(
        package_description, package_hashtags, packaging_opts=packaging_opts
    )

    return {
        "corrected_text": corrected_text,
        "segments": segments,
        "hook": main_hook,
        "hooks": hooks,
        "hooks_wide": hooks_wide,
        "hooks_vertical": hooks_vertical,
        "cta_wide": cta_wide,
        "cta_vertical": cta_vertical,
        "cta": cta_vertical or cta_wide,
        "package_title": package_title,
        "package_description": package_description,
        "package_hook": package_hook,
        "package_hashtags": package_hashtags,
        "intro": data.get("intro", {"start": 0, "end": 0}),
        "middle": _norm_middle(data.get("middle")),
        "outro": data.get("outro", {"start": 0, "end": 0}),
        "strong_words": data.get("strong_words", []),
        "subtitles": subtitles,
        "clips_for_shorts": clips,
    }


def _empty_analysis() -> dict:
    """Пустой ANALYSIS пакет."""
    return {
        "corrected_text": "",
        "segments": [],
        "hook": {"text": "", "timing": 0, "start": 0, "end": 0},
        "hooks": [],
        "hooks_wide": [],
        "hooks_vertical": [],
        "cta_wide": None,
        "cta_vertical": None,
        "cta": None,
        "intro": {"start": 0, "end": 0},
        "middle": [],
        "outro": {"start": 0, "end": 0},
        "package_title": "",
        "package_description": "",
        "package_hook": "",
        "package_hashtags": "",
        "strong_words": [],
        "subtitles": [],
        "clips_for_shorts": [],
    }