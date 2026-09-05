# VideoMaker FIX | 2026.09.05-r38 | 2026-09-05
# CHANGED: только keyword-group + punch; non-key groups не красят; hook без групп → 1–2 последних слова
# PREV: 2026.09.05-r37
# REPLACE: video_maker/engines/aisie_integration.py
"""AISIE интеграция — улучшение анализа через AISIE pipeline."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def enhance_analysis_with_aisie(
    analysis: dict,
    transcription: dict,
    video_size: tuple[int, int] = (1080, 1920),
    platform: str = "youtube_shorts",
    motion_score: float = 5.0,
    log_fn=None,
) -> dict:
    """Улучшить ANALYSIS пакет данными из AISIE pipeline.
    
    Добавляет/заменяет:
    - hooks с placement, animation, visual_weight
    - subtitles с правильным placement
    - validation_issues
    
    Args:
        analysis: Исходный ANALYSIS от Gemini
        transcription: Транскрипция от WhisperX
        video_size: Разрешение видео (w, h)
        platform: Платформа для профиля AISIE
        motion_score: Оценка движения в видео (1-20)
        log_fn: Функция логирования
    
    Returns:
        Обновленный analysis с данными AISIE
    """
    _log = log_fn or log.info
    
    try:
        from ..external.aisie.pipeline import AISIEPipeline
        from ..external.aisie.placement import PROFILES
    except ImportError as e:
        _log(f"[AISIE] Не удалось импортировать AISIE: {e}")
        return analysis
    
    platform_key = platform
    if platform_key not in PROFILES:
        # fallback: горизонталь → youtube, иначе shorts
        platform_key = "youtube_16_9" if video_size[0] >= video_size[1] and "youtube_16_9" in PROFILES else "youtube_shorts"
        if platform_key not in PROFILES:
            platform_key = next(iter(PROFILES.keys()))
        _log(f"[AISIE] Платформа → {platform_key}")

    segments = transcription.get("segments", [])
    if not segments:
        _log("[AISIE] Нет сегментов для обработки")
        return analysis

    aisie_segments = []
    word_timings = []
    for s in segments:
        aisie_segments.append({
            "text": s.get("text", ""),
            "start": s.get("start", 0),
            "end": s.get("end", 0),
        })
        for w in s.get("words") or []:
            word_timings.append({
                "word": (w.get("word") or w.get("text") or "").strip(),
                "start": w.get("start", 0),
                "end": w.get("end", 0),
            })

    try:
        _log(f"[AISIE] Запуск pipeline для {platform_key}, size={video_size}...")
        pipe = AISIEPipeline(platform=platform_key)
        plan = pipe.process(
            segments=aisie_segments,
            video_size=video_size,
            motion_score=motion_score,
            word_timings=word_timings or None,
        )
        
        # Добавляем AISIE данные в analysis
        if "aisie" not in analysis:
            analysis["aisie"] = {}
        
        analysis["aisie"]["hooks"] = plan.get("hooks", [])
        analysis["aisie"]["subtitles"] = plan.get("subtitles", [])
        analysis["aisie"]["validation_issues"] = plan.get("validation_issues", [])
        analysis["aisie"]["visual_load"] = plan.get("visual_load", {})
        analysis["aisie"]["safe_zones"] = plan.get("safe_zones", {})
        
        # Если в исходном analysis нет hooks/subtitles, используем AISIE
        if not analysis.get("hooks") and plan.get("hooks"):
            # Конвертируем AISIE hooks в формат pipeline
            hooks = []
            for h in plan["hooks"]:
                hooks.append({
                    "text": h.get("text", ""),
                    "start": h.get("start", 0),
                    "end": h.get("end", 0),
                    "timing": h.get("start", 0),
                    "type": h.get("type", ""),
                    "visual_weight": h.get("visual_weight", ""),
                    "animation": h.get("animation", ""),
                    "position_zone": h.get("position_zone", ""),
                    "y_percent": h.get("y_percent", 0),
                    "x_percent": h.get("x_percent", 50.0),
                    "semantic_groups": h.get("semantic_groups", []),
                })
            analysis["hooks"] = hooks
        
        if not analysis.get("subtitles") and plan.get("subtitles"):
            subtitles = []
            for s in plan["subtitles"]:
                subtitles.append({
                    "start": s.get("start", 0),
                    "end": s.get("end", 0),
                    "text": s.get("text", ""),
                    "style": s.get("visual_weight", "L0").lower(),
                })
            analysis["subtitles"] = subtitles
        
        _log(f"[AISIE] Добавлено хуков: {len(plan.get('hooks', []))}, субтитров: {len(plan.get('subtitles', []))}")

        # Обогатить strong_words из AISIE (karaoke neon color/scale)
        # keyword-group → последнее слово L3/L4; остальные слова группы → L2
        sw = list(analysis.get("strong_words") or [])
        existing = {(str(x.get("word") or "").strip().lower()) for x in sw if isinstance(x, dict)}
        added = 0

        def _add_sw(word: str, weight: str, start: float, end, source: str):
            nonlocal added
            key = word.strip().lower()
            if not key or key in existing:
                return
            existing.add(key)
            sw.append({
                "word": word.strip(),
                "visual_weight": weight,
                "start": float(start or 0),
                "end": float(end) if end else None,
                "source": source,
            })
            added += 1

        for h in plan.get("hooks") or []:
            hw = (h.get("visual_weight") or "L3").upper()
            if hw not in ("L1", "L2", "L3", "L4"):
                hw = "L3"
            groups = h.get("semantic_groups") or []
            if not groups:
                # без групп — только 1–2 последних содержательных слова хука, не вся фраза
                parts = [x for x in str(h.get("text") or "").split() if x.strip()]
                if not parts:
                    continue
                if len(parts) == 1:
                    _add_sw(parts[0], hw, h.get("start", 0), h.get("end"), "aisie_hook")
                else:
                    _add_sw(parts[-2], "L2", h.get("start", 0), h.get("end"), "aisie_hook")
                    _add_sw(parts[-1], hw if hw in ("L3", "L4") else "L3",
                            h.get("start", 0), h.get("end"), "aisie_hook")
                continue
            for g in groups:
                raw = g.get("words") or g.get("text") or ""
                parts = raw if isinstance(raw, list) else str(raw).split()
                parts = [str(x).strip() for x in parts if str(x).strip()]
                if not parts:
                    continue
                is_key = bool(g.get("is_keyword_group"))
                g_start = float(g.get("start") or h.get("start") or 0)
                g_end = g.get("end") or h.get("end")
                if is_key:
                    # словосочетание: поддержка L2, punch (последнее) — максимальный вес хука
                    for part in parts[:-1]:
                        _add_sw(part, "L2", g_start, g_end, "aisie_sg")
                    punch = "L4" if hw == "L4" else "L3"
                    _add_sw(parts[-1], punch, g_start, g_end, "aisie_keyword")
                # non-keyword groups: НЕ красим — иначе «все слова непонятно»

        if added:
            analysis["strong_words"] = sw
            _log(f"[AISIE] strong_words += {added} из hooks/semantic_groups (всего {len(sw)})")
        else:
            n_hooks = len(plan.get("hooks") or [])
            _log(f"[AISIE] strong_words не пополнены (hooks={n_hooks}); остаются Gemini: {len(sw)}")
        
    except Exception as e:
        _log(f"[AISIE] Ошибка: {e}")

    
    return analysis