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
# 503 → максимум 2 коротких retry (2с, 4с), затем следующий ключ
# Запоминаем последний успешный ключ и модель в ~/video_maker/cache/gemini_state.json

DEFAULT_MODEL_CHAIN = (
    "gemini-2.5-flash",      # приоритет по умолчанию
    "gemini-2.0-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-1.5-flash",
)

STATE_PATH = Path.home() / "video_maker" / "cache" / "gemini_state.json"

# Stage D: response cache key = hash(audio) + model + schema
ANALYSIS_SCHEMA_VERSION = "v1-package-hooks-subs-shorts"
ANALYSIS_CACHE_DIR = Path.home() / "video_maker" / "cache" / "gemini_analysis"


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
) -> str:
    """Gemini cache key = hash(audio) + model + schema (+ intro/series + text fingerprint)."""
    audio_fp = _audio_fingerprint(audio_path)
    text = ""
    if transcription:
        segs = transcription.get("segments") or []
        text = " ".join((s.get("text") or "") for s in segs[:50])
    text_fp = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]
    raw = (
        f"{audio_fp}|{model_name}|{ANALYSIS_SCHEMA_VERSION}|"
        f"intro={int(bool(intro_gemini))}|series={series_name or ''}|txt={text_fp}"
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
        tmp = str(path) + ".tmp"
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
        chain: list[str] = []
        if preferred:
            chain.append(preferred)
        for m in DEFAULT_MODEL_CHAIN:
            if m not in chain:
                chain.append(m)
        return chain

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



def _build_analysis_prompt(text: str, segments: list[dict], intro_gemini: bool = True, series_name: str = "") -> str:
    """Промпт Gemini: packaging полного видео + Shorts-стратег (Точка наблюдения)."""
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
- package_description: в духе:
  «В этом выпуске сериала «Тайный кризис человечества» на канале «Точка наблюдения» мы разбираем [тема].
  Вы узнаете, почему [интрига1], как [интрига2]...
  📍 В этом видео: [4–6 пунктов с таймкодами из источника]
  👇 Напишите в комментариях: [спорный вопрос]?
  🔔 Подписывайтесь на «Точка наблюдения»…»
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
- description: 1–2 предложения + призыв комментировать + отсылка на полную серию
  «Тайный кризис человечества» и канал «Точка наблюдения»
- hashtags: 5–8 через пробел; ОБЯЗАТЕЛЬНО #ТочкаНаблюдения и #ТайныйКризисЧеловечества
  + 3–6 нишевых
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
  "package_description": "Описание полного выпуска + таймкоды + вопрос + подписка",
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
      "description": "описание + комментарий + отсылка на сериал и канал",
      "hashtags": "#ТочкаНаблюдения #ТайныйКризисЧеловечества #tag3 #tag4 #tag5"
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



def analyze(
    transcription: dict,
    api_key: str = "",
    api_keys: list[str] | None = None,
    model_name: str = "gemini-2.5-flash",
    intro_gemini: bool = True,
    series_name: str = "",
    log_fn=None,
    audio_path: str = "",
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
    cache_key = _analysis_cache_key(
        audio_path or "",
        model_name or "gemini-2.5-flash",
        bool(intro_gemini),
        series_name or "",
        transcription,
    )
    cached = _analysis_cache_get(cache_key, log_fn=_log)
    if cached is not None:
        return cached

    full_text = " ".join(s.get("text", "") for s in segments)
    prompt = _build_analysis_prompt(full_text, segments, intro_gemini, series_name)

    km = GeminiKeyManager(keys, preferred_model=model_name, log_fn=_log)
    _log(f"[GEMINI] Анализ | ключей={km.n} | модель={km.current_model} | cache_key={cache_key[:12]}…")

    # лимиты: на один ключ для 503 — не больше 2 коротких retry
    max_503_per_key = 2
    consecutive_503 = 0
    json_retries = 0
    # общий потолок попыток = ключи × модели × (1 + 503 retries) — без бесконечного цикла
    max_attempts = max(km.n * len(km.model_chain) * 3, 6)
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
            analysis = _parse_analysis(raw, segments)
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
                consecutive_503 = 0
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

            # --- 503 / UNAVAILABLE: 2 коротких retry, потом ключ ---
            if (
                "503" in err_str
                or "UNAVAILABLE" in err_str
                or "high demand" in err_str.lower()
                or "overloaded" in err_str.lower()
            ):
                consecutive_503 += 1
                if consecutive_503 <= max_503_per_key:
                    wait = 2.0 * consecutive_503  # 2с, 4с — НЕ 10/20/40/80/120
                    _log(
                        f"[GEMINI] 503 temporary — короткий retry {consecutive_503}/"
                        f"{max_503_per_key} через {wait:.0f}с"
                    )
                    time.sleep(wait)
                    continue
                consecutive_503 = 0
                if not km.rotate_key("503 after short retries"):
                    if km.next_model():
                        km.exhausted.clear()
                        continue
                    raise RuntimeError(
                        "Gemini временно недоступен (503) на всех ключах. Попробуйте позже."
                    ) from e
                time.sleep(0.5)
                continue

            # --- invalid key ---
            if "API_KEY_INVALID" in err_str or "API key not valid" in err_str:
                consecutive_503 = 0
                if not km.rotate_key("invalid key"):
                    raise RuntimeError(
                        "Недействительный Gemini API ключ (все ключи)."
                    ) from e
                continue

            # --- model not found → следующая модель ---
            if "NOT_FOUND" in err_str and "model" in err_str.lower():
                consecutive_503 = 0
                _log(f"[GEMINI] Модель {model} недоступна")
                if km.next_model():
                    continue
                raise RuntimeError(
                    f"Модель Gemini недоступна: {model_name} и fallback-цепочка."
                ) from e

            # --- JSON parse ---
            if isinstance(e, (json.JSONDecodeError, ValueError)) and "JSON" in err_str:
                if json_retries < 1:
                    json_retries += 1
                    _log("[GEMINI] JSON parse error — повтор со строгим промптом")
                    prompt += (
                        "\n\nВАЖНО: Верни ТОЛЬКО валидный JSON без markdown, "
                        "без комментариев, без лишнего текста."
                    )
                    time.sleep(1.0)
                    continue
                raise

            _log(f"[GEMINI] Ошибка: {e}")
            raise

    raise RuntimeError("Gemini: превышено число попыток (ключи/модели)")



def _parse_analysis(raw: str, segments: list[dict]) -> dict:
    """Распарсить ответ Gemini в структурированный ANALYSIS."""
    try:
        # Убираем markdown code blocks
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        data = json.loads(text)
        return _normalize_analysis(data, segments)
    except (json.JSONDecodeError, IndexError) as e:
        log.error(f"[GEMINI] Ошибка парсинга JSON: {e}. Ответ: {raw[:500]}")
        return _empty_analysis()


def _normalize_analysis(data: dict, segments: list[dict]) -> dict:
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
    REQUIRED_TAGS = ["#ТочкаНаблюдения", "#ТайныйКризисЧеловечества"]

    def _ensure_hashtags(raw: str) -> str:
        tags = []
        for part in (raw or "").replace(",", " ").split():
            p = part.strip()
            if not p:
                continue
            if not p.startswith("#"):
                p = "#" + p.lstrip("#")
            if p not in tags:
                tags.append(p)
        for req in REQUIRED_TAGS:
            if req not in tags:
                tags.insert(0, req)
        # 5–12 тегов
        return " ".join(tags[:12])

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

        ht = _ensure_hashtags(str(c.get("hashtags") or ""))
        desc = str(c.get("description") or "").strip()
        if "Точка наблюдения" not in desc and "точке наблюдения" not in desc.lower():
            desc = (desc + " " if desc else "") + (
                "Полная серия «Тайный кризис человечества» на канале «Точка наблюдения»."
            )
        if "коммент" not in desc.lower() and "?" not in desc:
            desc = desc + " Напишите в комментариях, согласны ли вы."

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
    # гарантируем обязательные теги канала/сериала
    ph_tags = []
    for part in (package_hashtags or "").replace(",", " ").split():
        p = part.strip()
        if not p:
            continue
        if not p.startswith("#"):
            p = "#" + p.lstrip("#")
        if p not in ph_tags:
            ph_tags.append(p)
    for req in ("#ТочкаНаблюдения", "#ТайныйКризисЧеловечества"):
        if req not in ph_tags:
            ph_tags.insert(0, req)
    package_hashtags = " ".join(ph_tags[:12])

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
        "middle": data.get("middle", []),
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