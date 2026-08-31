"""Движок анализа — Gemini API."""
from __future__ import annotations

import json
import logging
import re
import time

log = logging.getLogger(__name__)


MAX_429_RETRIES = 4
MAX_503_RETRIES = 6  # high demand / UNAVAILABLE


def _retry_delay_seconds(message: str) -> float:
    """Достаёт retryDelay из ответа 429 ('Please retry in 37.89s')."""
    m = re.search(r"retry(?:\s+in)?\s*[\"']?\s*:?\s*([0-9]+(?:\.[0-9]+)?)s",
                  message, re.IGNORECASE)
    if not m:
        m = re.search(r"retryDelay[\"'\s:]+([0-9]+)s", message, re.IGNORECASE)
    return min(float(m.group(1)) + 2.0, 180.0) if m else 30.0


def _rotate_key(keys: list[str], key_index: int, log_fn=None) -> tuple[bool, int]:
    """Переключиться на следующий ключ. Возвращает (success, new_index)."""
    if key_index + 1 >= len(keys):
        return False, key_index
    new_index = key_index + 1
    if log_fn:
        log_fn(f"[GEMINI] Ключ [{key_index+1}/{len(keys)}] исчерпан, переключаюсь на ключ [{new_index+1}/{len(keys)}]")
    return True, new_index


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
A) hooks_wide (16:9) — 2–4 шт: умный/сериальный тон, 3–7 слов, start/end, показ 2–3.5с
B) hooks_vertical (9:16) — 2–4 шт: сильнее pattern-interrupt, 3–6 слов, ДРУГОЙ текст, чем wide
Legacy "hooks" = hooks_vertical.
type: QUESTION | CONTRADICTION | STATEMENT | CURIOSITY | IDENTITY | LOSS | REVELATION
Первый хук — начало ролика; остальные — точки спада/кульминации.

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
    model_name: str = "gemini-3.6-flash",
    intro_gemini: bool = True,
    series_name: str = "",
    log_fn=None,
) -> dict:
    """Единый вызов Gemini → пакет ANALYSIS. Поддержка ротации ключей и ретраев."""
    _log = log_fn or log.info
    _log(f"[GEMINI] Анализ моделью {model_name}")

    keys = []
    if api_keys:
        keys = [k.strip() for k in api_keys if k.strip()]
    if api_key and api_key not in keys:
        keys.insert(0, api_key)

    if not keys:
        _log("[GEMINI] API ключ не задан, пропускаем")
        return _empty_analysis()

    segments = transcription.get("segments", [])
    if not segments:
        _log("[GEMINI] Нет сегментов для анализа")
        return _empty_analysis()

    full_text = " ".join(s.get("text", "") for s in segments)

    prompt = _build_analysis_prompt(full_text, segments, intro_gemini, series_name)

    key_index = 0
    attempt = 0
    per_day_seen = False

    while key_index < len(keys):
        key = keys[key_index]
        try:
            from google import genai
            client = genai.Client(api_key=key)

            _log(f"[GEMINI] Запрос к модели {model_name} (ключ {key_index+1}/{len(keys)})")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )

            raw = response.text
            if not raw or not raw.strip():
                raise RuntimeError("Gemini вернул пустой ответ")

            analysis = _parse_analysis(raw, segments)
            _log(
                f"[GEMINI] OK: shorts={len(analysis.get('clips_for_shorts', []))} "
                f"hooks_w={len(analysis.get('hooks_wide') or [])} "
                f"hooks_v={len(analysis.get('hooks_vertical') or [])} "
                f"package_title={str(analysis.get('package_title') or '')[:50]!r}"
            )
            return analysis

        except Exception as e:
            err_str = str(e)
            _log(f"[GEMINI] Ошибка API: {err_str[:400]}")

            # 503 / UNAVAILABLE — временный пик нагрузки Google
            if (
                "503" in err_str
                or "UNAVAILABLE" in err_str
                or "high demand" in err_str.lower()
                or "experiencing high demand" in err_str.lower()
            ):
                attempt += 1
                if attempt <= MAX_503_RETRIES:
                    wait = min(120.0, 10.0 * (2 ** min(attempt - 1, 4)))  # 10,20,40,80,120...
                    _log(
                        f"[GEMINI] 503 UNAVAILABLE (high demand). "
                        f"Жду {wait:.0f}с, повтор {attempt}/{MAX_503_RETRIES}..."
                    )
                    time.sleep(wait)
                    continue
                # после ретраев — смена ключа если есть
                if key_index + 1 < len(keys):
                    key_index += 1
                    attempt = 0
                    _log(f"[GEMINI] 503: переключаюсь на ключ {key_index+1}/{len(keys)}")
                    time.sleep(3.0)
                    continue
                raise RuntimeError(
                    "Gemini временно недоступен (503 high demand) после нескольких попыток. "
                    "Подождите 5–15 минут или смените модель/ключ."
                ) from e

            # 429 / quota exhausted
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                per_day_seen = per_day_seen or "PerDay" in err_str

                # Есть запасной ключ — переключаемся
                if key_index + 1 < len(keys):
                    key_index += 1
                    attempt = 0
                    if log_fn:
                        log_fn(f"[GEMINI] Ключ [{key_index}/{len(keys)}] исчерпан, переключаюсь на следующий...")
                    time.sleep(2.0)
                    continue

                # Ключ один — ждём с экспоненциальным бэкоффом
                if attempt < MAX_429_RETRIES:
                    attempt += 1
                    wait = max(_retry_delay_seconds(err_str) * min(attempt, 3), 15.0)
                    if log_fn:
                        log_fn(f"[GEMINI] Лимит запросов (429). Жду {wait:.0f}с и повторяю (попытка {attempt}/{MAX_429_RETRIES})...")
                    time.sleep(wait)
                    continue

                tail = (" Суточный лимит free-тарифа — 20 запросов/день на модель; добавьте ещё ключи." if per_day_seen else "")
                raise RuntimeError(f"Gemini: квота исчерпана после {MAX_429_RETRIES} ожиданий.{tail}") from e

            # Invalid API key
            if "API_KEY_INVALID" in err_str or "API key not valid" in err_str:
                if key_index + 1 < len(keys):
                    key_index += 1
                    if log_fn:
                        log_fn("[GEMINI] Ключ недействителен, переключаюсь на следующий...")
                    continue
                raise RuntimeError("Недействительный Gemini API ключ. Проверьте ключ в Google AI Studio.") from e

            # Model not found
            if "NOT_FOUND" in err_str and "model" in err_str.lower():
                raise RuntimeError(f"Модель Gemini недоступна: {model_name}. Проверьте название модели.") from e

            # JSON parse error — retry once with stricter prompt
            if isinstance(e, (json.JSONDecodeError, ValueError)) and "JSON" in err_str:
                if attempt < 1:
                    attempt += 1
                    if log_fn:
                        log_fn("[GEMINI] Ошибка парсинга JSON, повторный запрос с строгими требованиями...")
                    prompt += "\n\nВАЖНО: Верни ТОЛЬКО валидный JSON без markdown, без комментариев, без лишнего текста."
                    time.sleep(2.0)
                    continue

            # Other errors — don't swallow, raise
            _log(f"[GEMINI] Ошибка: {e}")
            raise

    # Если вышли из цикла — все ключи исчерпаны
    _log("[GEMINI] Все ключи исчерпаны")
    raise RuntimeError("Все Gemini API ключи исчерпаны или недействительны")


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