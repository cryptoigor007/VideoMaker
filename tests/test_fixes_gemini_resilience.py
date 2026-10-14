"""Регрессии: устойчивость Gemini-цепочки (перегрузка 503, таймауты, отключённые модели).

Каждый тест сформулирован на желаемое поведение:
- 503 — это перегрузка МОДЕЛИ у Google, а не проблема ключа: один короткий retry,
  затем сразу следующая модель (ключи не «сжигаем»).
- Сетевые таймауты/обрывы — временные: ведут себя как 503, а не роняют весь прогон.
- 404 у отключённой модели — быстрый скип на следующую модель без повторов.
- Каталог моделей содержит актуальные модели (3.7/3.8/flash-latest) и не содержит
  давно отключённые (2.0/1.5); живучие lite-модели идут раньше перегружаемых флагманов.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

GOOD_JSON = json.dumps({
    "package_title": "Тестовый заголовок серии",
    "package_description": "Описание выпуска.",
    "package_hook": "Хук серии",
    "package_hashtags": "#ТочкаНаблюдения #ТайныйКризисЧеловечества #тест",
    "hooks_wide": [{"text": "Хук 16:9", "start": 0.0, "end": 2.5}],
    "hooks_vertical": [{"text": "Хук 9:16", "start": 0.0, "end": 2.5}],
    "clips_for_shorts": [{
        "text": "Текст клипа",
        "start": 0.0,
        "end": 15.0,
        "hook": "Хук клипа",
        "hook_start": 0.0,
        "hook_end": 2.0,
        "cta": "Напиши в комментариях",
        "cta_start": 10.0,
        "cta_end": 15.0,
        "title": "Клип",
        "description": "Описание клипа",
        "hashtags": "#ТочкаНаблюдения",
    }],
}, ensure_ascii=False)

SEGMENTS = [
    {"start": 0.0, "end": 3.0, "text": "Первый сегмент."},
    {"start": 3.0, "end": 6.0, "text": "Второй сегмент."},
]
TRANSCRIPTION = {"segments": SEGMENTS}


class ReadTimeout(Exception):
    """Имитация сетевого таймаута (как httpx.ReadTimeout у SDK)."""


def _run_analyze(monkeypatch, tmp_path, behaviors, *, preferred,
                 default_kind="ok", keys=("k1", "k2", "k3")):
    """Прогнать analyze() с фейковым genai.Client.

    behaviors: {model: "ok" | "503" | "timeout" | "404"}.
    Возвращает (result | exception, calls=[(key, model), ...]).
    """
    import google.genai as genai_mod
    import video_maker.engines.analysis as analysis

    calls: list[tuple[str, str]] = []

    class FakeModels:
        def __init__(self, key: str):
            self._key = key

        def generate_content(self, model, contents):
            calls.append((self._key, model))
            kind = behaviors.get(model, default_kind)
            if kind == "503":
                raise Exception(
                    "503 UNAVAILABLE. This model is currently experiencing high demand. "
                    "Spikes in demand are usually temporary. Please try again later."
                )
            if kind == "timeout":
                raise ReadTimeout("The read operation timed out")
            if kind == "404":
                raise Exception(
                    f"404 NOT_FOUND. This model models/{model} is no longer available "
                    "to new users. Please update your code."
                )
            return SimpleNamespace(text=GOOD_JSON)

    class FakeClient:
        def __init__(self, api_key=None, **_):
            self.models = FakeModels(api_key or "")

    monkeypatch.setattr(genai_mod, "Client", FakeClient)
    monkeypatch.setattr(analysis, "STATE_PATH", tmp_path / "gemini_state.json")
    monkeypatch.setattr(analysis, "ANALYSIS_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(analysis.time, "sleep", lambda _s: None)

    try:
        result = analysis.analyze(
            TRANSCRIPTION,
            api_keys=list(keys),
            model_name=preferred,
            log_fn=lambda *a, **k: None,
        )
        return result, calls
    except Exception as exc:  # noqa: BLE001
        return exc, calls


def test_503_skips_to_next_model_without_burning_keys(monkeypatch, tmp_path):
    """503 на флагмане: 2 попытки на одном ключе → сразу следующая модель."""
    result, calls = _run_analyze(
        monkeypatch, tmp_path,
        {"gemini-3.6-flash": "503"},
        preferred="gemini-3.6-flash",
    )
    assert not isinstance(result, Exception), result
    assert result["package_title"] == "Тестовый заголовок серии"
    assert calls == [
        ("k1", "gemini-3.6-flash"),
        ("k1", "gemini-3.6-flash"),
        ("k1", "gemini-3.5-flash-lite"),
    ]


def test_all_models_503_gives_up_without_key_rotation(monkeypatch, tmp_path):
    """Все модели перегружены: не жжём ключи впустую, каждая модель — ровно 2 попытки."""
    from video_maker.engines.analysis import AVAILABLE_GEMINI_MODELS

    result, calls = _run_analyze(
        monkeypatch, tmp_path, {}, default_kind="503",
        preferred="gemini-3.6-flash",
    )
    assert isinstance(result, RuntimeError)
    assert {key for key, _ in calls} == {"k1"}  # ключи не ротируются: 503 — свойство модели
    models = [m for _, m in calls]
    assert models[0] == models[1] == "gemini-3.6-flash"
    assert models[2] == models[3] == "gemini-3.5-flash-lite"
    assert len(calls) == 2 * len(AVAILABLE_GEMINI_MODELS)  # цепочка = каталог (pref совпал)


def test_network_timeout_is_transient_and_skips_model(monkeypatch, tmp_path):
    """Таймаут сети не роняет прогон: 1 retry, затем следующая модель."""
    result, calls = _run_analyze(
        monkeypatch, tmp_path,
        {"gemini-3.6-flash": "timeout"},
        preferred="gemini-3.6-flash",
    )
    assert not isinstance(result, Exception), result
    assert calls == [
        ("k1", "gemini-3.6-flash"),
        ("k1", "gemini-3.6-flash"),
        ("k1", "gemini-3.5-flash-lite"),
    ]


def test_retired_model_404_skips_to_next_model(monkeypatch, tmp_path):
    """Отключённая модель (404) пропускается сразу, без повторов."""
    result, calls = _run_analyze(
        monkeypatch, tmp_path,
        {"gemini-2.5-flash": "404"},
        preferred="gemini-2.5-flash",
    )
    assert not isinstance(result, Exception), result
    assert calls == [("k1", "gemini-2.5-flash"), ("k1", "gemini-3.6-flash")]


def test_catalog_has_current_models_and_no_retired_ancient():
    """Каталог: актуальные модели есть, давно отключённые — убраны, lite — первыми в запасе."""
    from video_maker.engines.analysis import AVAILABLE_GEMINI_MODELS as M

    assert "gemini-3.7-flash" in M
    assert "gemini-3.8-flash" in M
    assert "gemini-flash-latest" in M
    assert "gemini-3.5-flash-lite" in M

    assert "gemini-2.0-flash" not in M
    assert "gemini-1.5-flash" not in M
    assert "gemini-1.5-pro" not in M

    assert M.index("gemini-3.5-flash-lite") < M.index("gemini-3.5-flash")
    assert M.index("gemini-3.5-flash-lite") < M.index("gemini-3.8-flash")
