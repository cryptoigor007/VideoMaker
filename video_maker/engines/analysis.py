"""Движок анализа — Gemini API."""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)


def analyze(
    transcription: dict,
    api_key: str = "",
    api_keys: list[str] | None = None,
    model_name: str = "gemini-3.6-flash",
    intro_gemini: bool = True,
    log_fn=None,
) -> dict:
    """Единый вызов Gemini → пакет ANALYSIS. Поддержка ротации ключей."""
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

    for attempt, key in enumerate(keys):
        try:
            from google import genai
            client = genai.Client(api_key=key)

            prompt = _build_analysis_prompt(full_text, segments, intro_gemini)
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )

            raw = response.text
            analysis = _parse_analysis(raw, segments)
            _log(f"[GEMINI] Получено {len(analysis.get('clips_for_shorts', []))} клипов для Shorts")
            return analysis

        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                _log(f"[GEMINI] Ключ [{attempt+1}/{len(keys)}] исчерпан, пробуем следующий...")
                continue
            else:
                _log(f"[GEMINI] Ошибка: {e}")
                return _empty_analysis()

    _log("[GEMINI] Все ключи исчерпаны")
    return _empty_analysis()


def _build_analysis_prompt(text: str, segments: list[dict], intro_gemini: bool = True) -> str:
    """Построить промпт для анализа."""
    timings = "\n".join(
        f"[{s.get('start', 0):.1f}-{s.get('end', 0):.1f}] {s.get('text', '')}"
        for s in segments[:200]
    )

    intro_section = ""
    if intro_gemini:
        intro_section = """
ИНТРО:
- Это анимация логотипа (3-4 сек), без голоса
- Определи лучший момент для показа — любое подходящее место в видео
- Если не видишь подходящего момента — верни start=0, end=0
"""

    return f"""Ты — опытный контент-стратег для YouTube Shorts.

Разбей текст на 4-5 клипов для Shorts (15-60 сек каждый).

Для каждого клипа определи:
- text: полный текст фрагмента
- start: начало (секунды)
- end: конец (секунды)
- hook: хук-фраза (дословно из текста)
- title: заголовок (до 40 символов)
- description: описание + CTA
- hashtags: 5-8 хештегов
{intro_section}
Текст с таймингами:
{timings}

Верни JSON:
{{
  "corrected_text": "исправленный текст",
  "clips_for_shorts": [
    {{
      "text": "...",
      "start": 0.0,
      "end": 15.0,
      "hook": "...",
      "title": "...",
      "description": "...",
      "hashtags": "#tag1 #tag2"
    }}
  ],
  "hook": {{"text": "...", "timing": 0.0}},
  "intro": {{"start": 0.0, "end": 3.5}},
  "middle": [{{"start": 10.0, "end": 20.0}}],
  "outro": {{"start": 25.0, "end": 30.0}},
  "strong_words": [
    {{"word": "...", "timing": 0.0, "caps": true, "color": "#FF6B00"}}
  ],
  "subtitles": [
    {{"start": 0.0, "end": 2.0, "text": "...", "style": "normal"}}
  ]
}}"""


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
    except (json.JSONDecodeError, IndexError):
        return _empty_analysis()


def _normalize_analysis(data: dict, segments: list[dict]) -> dict:
    """Нормализовать ANALYSIS пакет."""
    return {
        "corrected_text": data.get("corrected_text", ""),
        "segments": segments,
        "hook": data.get("hook", {"text": "", "timing": 0}),
        "intro": data.get("intro", {"start": 0, "end": 0}),
        "middle": data.get("middle", []),
        "outro": data.get("outro", {"start": 0, "end": 0}),
        "strong_words": data.get("strong_words", []),
        "subtitles": data.get("subtitles", []),
        "clips_for_shorts": data.get("clips_for_shorts", []),
    }


def _empty_analysis() -> dict:
    """Пустой ANALYSIS пакет."""
    return {
        "corrected_text": "",
        "segments": [],
        "hook": {"text": "", "timing": 0},
        "intro": {"start": 0, "end": 0},
        "middle": [],
        "outro": {"start": 0, "end": 0},
        "strong_words": [],
        "subtitles": [],
        "clips_for_shorts": [],
    }
