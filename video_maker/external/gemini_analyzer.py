"""Модуль анализа текста с помощью Google Gemini."""
import json
import re
import time
from pathlib import Path

from google import genai


class GeminiAnalyzer:
    MAX_429_RETRIES = 4

    def __init__(self, api_keys, model_name=None, logger=None, audit_logger=None):
        if isinstance(api_keys, str):
            keys = [api_keys]
        else:
            keys = [str(k) for k in (api_keys or [])]
        self.api_keys = [k.strip() for k in keys if k and k.strip()]
        if not self.api_keys:
            raise ValueError("Gemini API key is empty")
        self._key_index = 0
        self.client = genai.Client(api_key=self.api_keys[0])
        self.model_name = (model_name or "gemini-3.6-flash").strip()
        self.logger = logger
        self.audit_logger = audit_logger

    @property
    def api_key(self):
        return self.api_keys[self._key_index]

    def _rotate_key(self):
        """Переключиться на следующий ключ. False, если запасных нет."""
        if self._key_index + 1 >= len(self.api_keys):
            return False
        self._key_index += 1
        self.client = genai.Client(api_key=self.api_keys[self._key_index])
        return True

    def _audit(self, event, **data):
        if self.audit_logger:
            self.audit_logger({"event": event, **data})

    @staticmethod
    def _safe_metadata(response):
        metadata = {}
        for name in ("usage_metadata", "prompt_feedback", "candidates"):
            value = getattr(response, name, None)
            if value is not None:
                try:
                    metadata[name] = value.to_json_dict() if hasattr(value, "to_json_dict") else str(value)
                except (TypeError, ValueError, AttributeError):
                    metadata[name] = str(value)
        return metadata

    @staticmethod
    def _retry_delay_seconds(message):
        """Достаёт retryDelay из ответа 429 ('Please retry in 37.89s')."""
        m = re.search(r"retry(?:\s+in)?\s*[\"']?\s*:?\s*([0-9]+(?:\.[0-9]+)?)s",
                      message, re.IGNORECASE)
        if not m:
            m = re.search(r"retryDelay[\"'\s:]+([0-9]+)s", message, re.IGNORECASE)
        return min(float(m.group(1)) + 2.0, 180.0) if m else 30.0

    def _generate_content(self, contents, phase="generate_content"):
        if self.logger:
            self.logger(f"Gemini: используется модель {self.model_name}")
        self._audit("request", phase=phase, model=self.model_name, prompt=contents, prompt_chars=len(contents))
        attempt = 0
        per_day_seen = False
        while True:
            try:
                response = self.client.models.generate_content(model=self.model_name, contents=contents)
                break
            except Exception as error:
                message = str(error)
                self._audit("error", phase=phase, model=self.model_name,
                            key_index=self._key_index, error=message)
                if "API_KEY_INVALID" in message or "API key not valid" in message:
                    if not self._rotate_key():
                        raise RuntimeError("Недействительный Gemini API/Auth key. Создайте новый ключ в Google AI Studio и проверьте GOOGLE_API_KEY.") from error
                    if self.logger:
                        self.logger(f"Gemini: ключ #{self._key_index} недействителен, переключаюсь на следующий", "warning")
                    continue
                if "NOT_FOUND" in message and "model" in message.lower():
                    raise RuntimeError(f"Модель Gemini недоступна: {self.model_name}. Выберите актуальную модель в настройках.") from error
                if "429" in message or "RESOURCE_EXHAUSTED" in message:
                    per_day_seen = per_day_seen or "PerDay" in message
                    # 1) есть запасной ключ — лимиты у каждого свои
                    if self._rotate_key():
                        attempt = 0
                        if self.logger:
                            self.logger(
                                f"Gemini: квота на ключе исчерпана (429) — "
                                f"переключаюсь на ключ #{self._key_index + 1} "
                                f"из {len(self.api_keys)}", "warning")
                        self._audit("key_rotated", phase=phase,
                                    new_key_index=self._key_index)
                        time.sleep(2.0)
                        continue
                    # 2) ключ один: окно скользящее — ждём и повторяем
                    if attempt < self.MAX_429_RETRIES:
                        attempt += 1
                        wait = max(self._retry_delay_seconds(message)
                                   * min(attempt, 3), 15.0)
                        if self.logger:
                            self.logger(
                                f"Gemini: лимит запросов (429). Жду {wait:.0f}с и "
                                f"повторяю (попытка {attempt}/{self.MAX_429_RETRIES})...",
                                "warning")
                        self._audit("rate_limit_wait", phase=phase,
                                    model=self.model_name, delay=wait, attempt=attempt)
                        time.sleep(wait)
                        continue
                    tail = (" Суточный лимит free-тарифа — 20 запросов/день на модель; "
                            "добавьте ещё ключи через запятую в настройках."
                            if per_day_seen else "")
                    raise RuntimeError(
                        f"Gemini: квота исчерпана даже после {self.MAX_429_RETRIES} ожиданий.{tail}"
                    ) from error
                raise
        actual_model = getattr(response, "model_version", None)
        response_text = response.text or ""
        self._audit("response", phase=phase, model=self.model_name, actual_model=actual_model, response=response_text, response_chars=len(response_text), metadata=self._safe_metadata(response))
        if self.logger and actual_model and actual_model != self.model_name:
            self.logger(f"Gemini: фактическая версия ответа {actual_model}")
        return response

    def analyze_text(self, text, prompt_path=None):
        if prompt_path is None:
            prompt_path = Path(__file__).parent / "prompt.txt"
        prompt = Path(prompt_path).read_text(encoding="utf-8")
        full_prompt = prompt.replace("[ВСТАВЬ СЮДА ТЕКСТ]", text)
        response = self._generate_content(full_prompt, "analyze_text")
        if not response.text:
            raise RuntimeError("Gemini вернул пустой ответ")
        return response.text

    def normalize_text(self, text):
        prompt = Path(__file__).parent / "prompt_auto.txt"
        template = prompt.read_text(encoding="utf-8")
        full_prompt = template.replace("[ВСТАВЬ СЮДА ТЕКСТ]", str(text))
        if template == full_prompt:
            full_prompt += "\n\nВходная транскрипция:\n" + str(text)
        full_prompt += "\n\nВыполни ЭТАП 1 и верни JSON."
        response = self._generate_content(full_prompt, "normalize_text")
        if not response.text:
            raise RuntimeError("Gemini вернул пустой ответ")
        parsed = self._parse_json(response.text)
        canonical = self._canonicalize_normalized(parsed)
        self._audit("parsed", phase="normalize_text", parsed=canonical)
        return canonical

    def analyze_full(self, text, source_timings=None):
        """ОДИН запрос на оба этапа: ЭТАП 1 (нормализация) + ЭТАП 2 (план).
        Возвращает {"normalized": {...}, "plan": {...}}."""
        template = (Path(__file__).parent / "prompt_auto.txt").read_text(encoding="utf-8")
        context = str(text)
        if source_timings:
            context += "\n\nИсходные таймкоды:\n" + json.dumps(source_timings, ensure_ascii=False)
        full_prompt = template.replace("[ВСТАВЬ СЮДА ТЕКСТ]", context)
        if full_prompt == template:
            full_prompt += "\n\nВходная транскрипция:\n" + context
        full_prompt += (
            "\n\nВАЖНО: выполни СРАЗУ ОБА ЭТАПА (1 и 2) в этом же ответе и верни "
            "ОДИН общий JSON-объект со всеми ключами сразу:\n"
            '{"text": "нормализованный текст", '
            '"sentences": [{"start": .., "end": .., "text": "..."}], '
            '"global_style": {...}, '
            '"cta": "вопрос для комментариев из ЭТАПА 2", '
            '"clips": [{...поля ЭТАПА 2..., "start":.., "end":.., "text":"..."}]}\n'
            "Таймкоды sentences и clips бери строго из исходных таймкодов. "
            "Никакого текста вне JSON."
        )
        last_error = None
        for attempt in (1, 2):
            try:
                response = self._generate_content(full_prompt, "analyze_full")
                if not response.text:
                    raise RuntimeError("Gemini вернул пустой ответ")
                parsed = self._parse_json(response.text)
                normalized = self._canonicalize_normalized(parsed)
                plan = self._normalize_plan(parsed)
                if not plan["clips"]:
                    raise RuntimeError("в ответе нет клипов плана")
                if not normalized.get("sentences"):
                    raise RuntimeError("в ответе нет предложений нормализации")
                self._audit("parsed", phase="analyze_full", attempt=attempt,
                            sentences=len(normalized["sentences"]),
                            clips=len(plan["clips"]))
                return {"normalized": normalized, "plan": plan}
            except (RuntimeError, ValueError, json.JSONDecodeError) as error:
                last_error = error
                self._audit("retry", phase="analyze_full", attempt=attempt, error=str(error))
                if self.logger:
                    self.logger(f"Gemini: попытка {attempt} не удалась ({error}), повтор...")
        raise RuntimeError(f"Gemini analyze_full не удался после 2 попыток: {last_error}") from last_error

    @staticmethod
    def _canonicalize_normalized(parsed):
        """Приводит ответ ЭТАПА 1 к виду {"text": ..., "sentences": [...]}."""
        if isinstance(parsed, dict) and ("text" in parsed or "sentences" in parsed):
            sentences = parsed.get("sentences") or []
            if not isinstance(sentences, list):
                sentences = []
            return {"text": parsed.get("text") or "", "sentences": sentences}
        if isinstance(parsed, list):
            sentences = [s for s in parsed if isinstance(s, dict) and s.get("text")]
            return {"text": " ".join(s["text"] for s in sentences), "sentences": sentences}
        if isinstance(parsed, dict) and "clips" in parsed:
            sentences = []
            for clip in parsed.get("clips") or []:
                if not isinstance(clip, dict):
                    continue
                text = (clip.get("text") or "").strip()
                if text:
                    sentences.append(
                        {
                            "start": clip.get("start", 0),
                            "end": clip.get("end", clip.get("start", 0)),
                            "text": text,
                        }
                    )
            return {"text": " ".join(s["text"] for s in sentences), "sentences": sentences}
        return {"text": str(parsed), "sentences": []}

    def plan_clips(self, normalized_text, source_timings=None):
        prompt = Path(__file__).parent / "prompt_auto.txt"
        context = json.dumps(normalized_text, ensure_ascii=False) if isinstance(normalized_text, (dict, list)) else str(normalized_text)
        if source_timings:
            context += "\n\nИсходные таймкоды:\n" + json.dumps(source_timings, ensure_ascii=False)
        template = prompt.read_text(encoding="utf-8")
        full_prompt = template.replace("[ВСТАВЬ СЮДА ТЕКСТ]", context)
        if template == full_prompt:
            full_prompt += "\n\nВход для ЭТАПА 2:\n" + context
        full_prompt += "\n\nВыполни ЭТАП 2 и верни JSON."
        last_error = None
        for attempt in (1, 2):
            try:
                response = self._generate_content(full_prompt, "plan_clips")
                if not response.text:
                    raise RuntimeError("Gemini вернул пустой ответ")
                parsed = self._parse_json(response.text)
                plan = self._normalize_plan(parsed)
                if not plan["clips"]:
                    raise RuntimeError("Gemini не вернул ни одного валидного клипа")
                self._audit("parsed", phase="plan_clips", attempt=attempt, parsed=plan)
                return plan
            except (RuntimeError, ValueError, json.JSONDecodeError) as error:
                last_error = error
                self._audit("retry", phase="plan_clips", attempt=attempt, error=str(error))
                if self.logger:
                    self.logger(f"Gemini: попытка {attempt} не удалась ({error}), повтор...")
        raise RuntimeError(f"Gemini plan_clips не удался после 2 попыток: {last_error}") from last_error

    @staticmethod
    def _normalize_plan(parsed):
        """Приводит план к безопасному виду: пропущенные поля заменяет
        дефолтами, клипы без start/end/text отбрасывает."""
        defaults = {
            "hook": "",
            "hook_variants": [],
            "hook_type": "general",
            "hook_style": "phrase_reveal",
            "title": "",
            "description": "",
            "hashtags": "",
            "style": "static",
            "emotion": "curiosity",
            "accent_words": [],
            "punch_words": [],
        }
        raw_clips = parsed.get("clips", []) if isinstance(parsed, dict) else parsed
        clips = []
        for raw in raw_clips or []:
            if not isinstance(raw, dict):
                continue
            try:
                start = float(raw.get("start"))
                end = float(raw.get("end"))
            except (TypeError, ValueError):
                continue
            text = str(raw.get("text") or "").strip()
            if not text or end <= start:
                continue
            clip = {"start": start, "end": end, "text": text}
            for field, default in defaults.items():
                value = raw.get(field)
                if value in (None, ""):
                    clip[field] = default
                elif field in ("hook_variants", "accent_words", "punch_words"):
                    clip[field] = value if isinstance(value, list) else default
                else:
                    clip[field] = str(value).strip()
            if not clip["title"]:
                clip["title"] = text[:40]
            clips.append(clip)
        global_style = parsed.get("global_style", {}) if isinstance(parsed, dict) else {}
        return {"clips": clips, "global_style": global_style if isinstance(global_style, dict) else {}}

    def _parse_json(self, raw_text):
        blocks = re.findall(r"```(?:json)?\s*(.*?)```", raw_text, re.DOTALL | re.IGNORECASE)
        candidates = blocks or [raw_text]
        last_error = None
        for candidate in reversed(candidates):
            try:
                return json.loads(candidate.strip())
            except json.JSONDecodeError as error:
                last_error = error
        match = re.search(r"(\{.*\}|\[.*\])", raw_text.strip(), re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError as error:
                last_error = error
        raise RuntimeError(f"Gemini не вернул валидный JSON: {last_error}. Начало ответа: {raw_text[:300]!r}") from last_error

    def parse_response(self, raw_text):
        clips = []
        pattern = re.compile(r"(?P<text>.+?)\nЗаголовок \(до 40 символов\):\s*\n(?P<title>.+?)\nОписание \+ CTA:\s*\n(?P<desc>.+?)\nХэштеги \(5–8 шт\):\s*\n(?P<tags>.+?)\n(?:Хук:\s*\n(?P<hook>.+?))?\n(?:Хук-варианты:\s*\n(?P<hook_variants>.+?))?\n(?:Тип визуализации:\s*\n(?P<visual_type>.+?))?\n(?:Акцентные слова:\s*\n(?P<accent_words>.+?))?\n(?:Панч-слова:\s*\n(?P<punch_words>.+?))?(?:\nОбработка отсылки|\nПочему сработает|$)", re.DOTALL)
        for match in pattern.finditer(raw_text):
            clip = {"text": match.group("text").strip(), "title": match.group("title").strip(), "description": match.group("desc").strip(), "hashtags": match.group("tags").strip(), "hook": match.group("hook").strip() if match.group("hook") else "", "hook_variants": match.group("hook_variants").split("|") if match.group("hook_variants") else [], "visual_type": match.group("visual_type").strip() if match.group("visual_type") else "b-roll", "accent_words": [], "punch_words": [], "timecode_est": ""}
            for field in ("accent_words", "punch_words"):
                value = match.group(field)
                if value:
                    clip[field] = [word.strip() for word in value.replace("|", ",").split(",") if word.strip()]
            clips.append(clip)
        return {"clips": clips}
