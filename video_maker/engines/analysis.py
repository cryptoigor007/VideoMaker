"""Движок анализа — Gemini API."""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)


def analyze(
    transcription: dict,
    api_key: str = "",
    model_name: str = "gemini-2.5-flash",
    log_fn=None,
) -> dict:
    """Единый вызов Gemini → пакет ANALYSIS."""
    _log = log_fn or log.info
    _log(f"[GEMINI] Анализ моделью {model_name}")

    if not api_key:
        _log("[GEMINI] API ключ не задан, пропускаем")
        return _empty_analysis()

    segments = transcription.get("segments", [])
    if not segments:
        _log("[GEMINI] Нет сегментов для анализа")
        return _empty_analysis()

    # Собираем текст
    full_text = " ".join(s.get("text", "") for s in segments)

    try:
        from google import genai
        client = genai.Client(api_key=api_key)

        prompt = _build_analysis_prompt(full_text, segments)
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
        )

        raw = response.text
        analysis = _parse_analysis(raw, segments)
        _log(f"[GEMINI] Получено {len(analysis.get('clips_for_shorts', []))} клипов для Shorts")
        return analysis

    except Exception as e:
        _log(f"[GEMINI] Ошибка: {e}")
        return _empty_analysis()


def _build_analysis_prompt(text: str, segments: list[dict]) -> str:
    """Построить промпт для анализа."""
    timings = "\n".join(
        f"[{s.get('start', 0):.1f}-{s.get('end', 0):.1f}] {s.get('text', '')}"
        for s in segments[:200]
    )

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
  "intro": {{"start": 0.0, "end": 5.0}},
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
