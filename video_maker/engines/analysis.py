"""Движок анализа — Gemini API."""
from __future__ import annotations

import json
import logging
import re
import time

log = logging.getLogger(__name__)


MAX_429_RETRIES = 4


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
    """Построить промпт для анализа."""
    timings = "\n".join(
        f"[{s.get('start', 0):.1f}-{s.get('end', 0):.1f}] {s.get('text', '')}"
        for s in segments
    )

    series_context = f"\nСерия: {series_name}" if series_name else ""

    intro_section = ""
    if intro_gemini:
        intro_section = """
ИНТРО:
- Это анимация логотипа (3-4 сек), без голоса
- Определи лучший момент для показа — любое подходящее место в видео
- Если не видишь подходящего момента — верни start=0, end=0
"""

    return f"""Ты — senior short-form content strategist и copywriter для YouTube / TikTok / Reels / YouTube 16:9.
Канал: «Точка наблюдения». Сериал: «Тайный кризис человечества».{series_context}

Роль: эксперт по retention и packaging. Хуки должны УДЕРЖИВАТЬ внимание:
pattern interrupt, curiosity gap, contradiction, stakes, identity, open loop.

Критерии сильного хука: конфликт/вопрос за 1с, незакрытый loop, без воды («сегодня поговорим…»).

═══════════════════════════════════════
1) ХУКИ ДЛЯ ПОЛНОГО ВИДЕО (wide + vertical)
═══════════════════════════════════════
- Дай 2–4 хука в точках retention-риска (начало, спад, кульминация).
- 3–10 слов, почти дословно из транскрипта, ударный порядок слов.
- Обязательно start и end (сек) — показ 2–3.5с с начала фразы.
- type: QUESTION | CONTRADICTION | STATEMENT | CURIOSITY | IDENTITY | LOSS | REVELATION.
- visual_weight: L3 или L4.

═══════════════════════════════════════
2) SHORTS (отдельный продукт под каждую нарезку)
═══════════════════════════════════════
- 3–6 клипов по 15–55 секунд, каждый — законченная мысль.
- У каждого Shorts СВОЙ хук (hook + hook_start + hook_end) — не копируй общий.
- title ≤ 40 символов, интрига без кликбейта-вранья.
- description: 1–2 предложения + CTA (вопрос зрителю / «напиши в комментариях»).
- hashtags: 5–8 штук, смесь нишевых и широких, на языке контента.
- Внутренняя оценка ≥ 8/10, слабые отбрасывай.

═══════════════════════════════════════
3) СИЛЬНЫЕ СЛОВА + СУБТИТРЫ
═══════════════════════════════════════
- strong_words: 5–15 ударных слов/фраз с точным timing (секунды), caps true/false, color #RRGGBB.
- subtitles: крупные смысловые куски 1.5–4 сек (для fallback), не дроби на каждое слово.

{intro_section}
Текст с таймингами:
{timings}

Верни ТОЛЬКО валидный JSON (без markdown, без пояснений):
{{
  "corrected_text": "полный исправленный текст с пунктуацией",
  "hooks": [
    {{
      "text": "текст хука",
      "start": 0.0,
      "end": 2.5,
      "type": "CURIOSITY",
      "visual_weight": "L3"
    }}
  ],
  "hook": {{"text": "главный хук всего ролика", "start": 0.0, "end": 2.5, "timing": 0.0}},
  "clips_for_shorts": [
    {{
      "text": "полный текст этого Short",
      "start": 0.0,
      "end": 18.0,
      "hook": "хук именно этого Short",
      "hook_start": 0.0,
      "hook_end": 2.0,
      "title": "заголовок ≤40",
      "description": "описание + CTA",
      "hashtags": "#tag1 #tag2 #tag3 #tag4 #tag5"
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
            _log(f"[GEMINI] Получено {len(analysis.get('clips_for_shorts', []))} клипов для Shorts")
            return analysis

        except Exception as e:
            err_str = str(e)

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

    # Хуки: массив + legacy single hook
    hooks = data.get("hooks") or []
    if not hooks:
        h = data.get("hook") or {}
        if isinstance(h, dict) and h.get("text"):
            start = float(h.get("start", h.get("timing", 0)) or 0)
            end = float(h.get("end", start + 2.5) or (start + 2.5))
            hooks = [{
                "text": h.get("text", ""),
                "start": start,
                "end": end,
                "timing": start,
                "type": h.get("type", "STATEMENT"),
                "visual_weight": h.get("visual_weight", "L3"),
            }]
    else:
        norm_hooks = []
        for h in hooks:
            if not isinstance(h, dict) or not h.get("text"):
                continue
            start = float(h.get("start", h.get("timing", 0)) or 0)
            end = float(h.get("end", start + 2.5) or (start + 2.5))
            norm_hooks.append({
                "text": h.get("text", ""),
                "start": start,
                "end": end,
                "timing": start,
                "type": h.get("type", "STATEMENT"),
                "visual_weight": h.get("visual_weight", "L3"),
            })
        hooks = norm_hooks

    main_hook = data.get("hook") or (hooks[0] if hooks else {"text": "", "timing": 0, "start": 0, "end": 0})
    if isinstance(main_hook, dict) and "start" not in main_hook:
        t = float(main_hook.get("timing", 0) or 0)
        main_hook = {**main_hook, "start": t, "end": float(main_hook.get("end", t + 2.5))}

    # Shorts: нормализуем hook_start/hook_end
    clips = []
    for c in data.get("clips_for_shorts") or []:
        if not isinstance(c, dict):
            continue
        hs = float(c.get("hook_start", c.get("start", 0)) or 0)
        he = float(c.get("hook_end", hs + 2.0) or (hs + 2.0))
        clips.append({
            **c,
            "hook": c.get("hook", ""),
            "hook_start": hs,
            "hook_end": he,
        })

    return {
        "corrected_text": corrected_text,
        "segments": segments,
        "hook": main_hook,
        "hooks": hooks,
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
        "intro": {"start": 0, "end": 0},
        "middle": [],
        "outro": {"start": 0, "end": 0},
        "strong_words": [],
        "subtitles": [],
        "clips_for_shorts": [],
    }