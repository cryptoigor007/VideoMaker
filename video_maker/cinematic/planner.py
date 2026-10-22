"""EditPlan builder — reuses existing transcript / analysis / B-roll mapping.

Never re-runs Whisper or Gemini.
Deterministic for the same inputs + algorithm_version + seed.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .config import CinematicSettings
from .edit_plan import EditPlan, Scene, CropRect, MotionKeyframe


def _stable_id(*parts: str) -> str:
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]
    return f"sc_{h}"


def _hash_obj(obj: Any) -> str:
    return hashlib.sha256(str(obj).encode()).hexdigest()[:16]



def _tokenize(text: str) -> set[str]:
    import re
    return {t for t in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]{3,}", (text or "").lower())}


def _bigrams(tokens: set[str] | list[str]) -> set[str]:
    seq = list(tokens) if isinstance(tokens, set) else list(tokens)
    # For sets order is arbitrary — use sorted for stability
    if isinstance(tokens, set):
        seq = sorted(tokens)
    return {f"{seq[i]}_{seq[i+1]}" for i in range(len(seq) - 1)} if len(seq) > 1 else set()



# Lightweight synonym expansion (no neural model) for RU/EN B-roll tags
_SYNONYMS: dict[str, set[str]] = {
    "ocean": {"sea", "water", "wave", "waves", "море", "океан", "волны", "вода", "beach", "пляж"},
    "city": {"urban", "street", "город", "улица", "traffic", "skyline", "метро", "subway"},
    "nature": {"forest", "tree", "trees", "лес", "природа", "деревья", "mountain", "горы", "field"},
    "people": {"person", "crowd", "человек", "люди", "face", "лицо", "woman", "man", "девушка"},
    "night": {"dark", "evening", "ночь", "вечер", "neon", "lights", "огни"},
    "work": {"office", "работа", "офис", "desk", "computer", "laptop", "meeting"},
    "home": {"house", "дом", "квартира", "room", "interior", "kitchen", "кухня"},
    "road": {"car", "highway", "дорога", "машина", "drive", "авто", "travel"},
    "sky": {"cloud", "clouds", "небо", "облака", "sunset", "sunrise", "закат"},
    "money": {"finance", "деньги", "банк", "cash", "business", "wealth", "богатство"},
    "love": {"romance", "couple", "любовь", "пара", "wedding", "свадьба"},
    "fear": {"dark", "horror", "страх", "тревога", "shadow", "тень"},
    "tech": {"phone", "screen", "технология", "гаджет", "ai", "robot"},
    "health": {"sport", "run", "здоровье", "спорт", "gym", "yoga"},
    "food": {"eat", "restaurant", "еда", "ресторан", "coffee", "кофе"},
}


def _expand_synonyms(tokens: set[str]) -> set[str]:
    out = set(tokens)
    for t in list(tokens):
        for key, syns in _SYNONYMS.items():
            if t == key or t in syns:
                out.add(key)
                out |= syns
    return out

def score_clip_against_text(
    clip: dict,
    text: str,
    extra_terms: str = "",
    *,
    desired_duration: float | None = None,
) -> float:
    """
    Lightweight lexical overlap in [0, 1] + optional duration fit.
    Uses tags/description/text/path — not a neural semantic model.
    extra_terms: analysis topics/keywords joined string.
    Returns 0 when metadata is missing (caller keeps original order).
    """
    parts = [
        str(clip.get("tags") or ""),
        str(clip.get("description") or ""),
        str(clip.get("text") or ""),
        str(Path(str(clip.get("path") or clip.get("source") or "")).stem),
    ]
    clip_tok: set[str] = set()
    for part in parts:
        clip_tok |= _tokenize(part)
    text_tok = _expand_synonyms(_tokenize(text) | _tokenize(extra_terms))
    clip_tok = _expand_synonyms(clip_tok)
    if not text_tok or not clip_tok:
        lex = 0.0
    else:
        uni = len(clip_tok & text_tok) / max(1, len(text_tok))
        # bigram overlap (stable sort for set→list)
        cb = _bigrams(clip_tok)
        tb = _bigrams(text_tok)
        bi = (len(cb & tb) / max(1, len(tb))) if tb else 0.0
        lex = min(1.0, 0.7 * uni + 0.3 * bi)

    # Duration fitness: prefer clips that can cover the slot without heavy repeat
    dur_score = 0.0
    if desired_duration and desired_duration > 0:
        try:
            cd = float(clip.get("duration") or clip.get("end", 0) - clip.get("start", 0) or 0)
        except (TypeError, ValueError):
            cd = 0.0
        if cd > 0.05:
            # ideal: clip >= 0.7 * slot and not hugely longer than 2.5x slot
            ratio = cd / desired_duration
            if 0.7 <= ratio <= 2.5:
                dur_score = 1.0
            elif ratio < 0.7:
                dur_score = max(0.0, ratio / 0.7)
            else:
                dur_score = max(0.0, 1.0 - (ratio - 2.5) / 3.0)

    # Role boost from clip metadata
    role = str(clip.get("role") or "").lower()
    role_boost = 0.05 if role in ("hook", "emphasis", "intro") else 0.0

    return min(1.0, 0.75 * lex + 0.20 * dur_score + role_boost)


def _analysis_terms(analysis: dict | None) -> str:
    if not analysis or not isinstance(analysis, dict):
        return ""
    bits: list[str] = []
    for key in ("topics", "keywords", "tags", "highlights", "entities"):
        val = analysis.get(key)
        if isinstance(val, list):
            bits.extend(str(x) for x in val)
        elif isinstance(val, str):
            bits.append(val)
    return " ".join(bits)


def _scene_windows_from_segments(
    segs: Sequence[dict[str, Any]], target_duration: float
) -> list[tuple[float, float]]:
    """Непрерывные окна сцен из сегментов транскрипта (последняя — до конца).

    Границы — старты сегментов: пауза между репликами относится к предыдущей
    сцене, поэтому визуальный ряд не «съезжает» и не оставляет пустых окон.
    """
    n = len(segs)
    if n == 0:
        return []
    tgt = float(target_duration or 0.0)
    windows: list[tuple[float, float]] = []
    for i, seg in enumerate(segs):
        try:
            start = 0.0 if i == 0 else float(seg.get("start", 0.0) or 0.0)
        except (TypeError, ValueError):
            start = 0.0
        if i + 1 < n:
            try:
                end = float(segs[i + 1].get("start", start + 1.0) or (start + 1.0))
            except (TypeError, ValueError):
                end = start + 1.0
        else:
            try:
                end = max(tgt, float(seg.get("end", tgt) or tgt))
            except (TypeError, ValueError):
                end = tgt
        if end <= start + 0.05:
            end = start + 0.5
        windows.append((start, end))
    # Последнее окно должно доходить ровно до конца озвучки.
    if tgt > windows[-1][0] + 0.05:
        windows[-1] = (windows[-1][0], tgt)
    return windows


def _pad_bound_for_transitions(
    slices: Sequence[float], settings: CinematicSettings
) -> list[float]:
    """Добавить к каждому плану длительность xfade-перехода к следующему.

    apply_rhythm строит выход как Σ(d_i − overlap_i). Если d_i = окно_i +
    overlap_{i+1}, то старт каждой сцены остаётся ровно на границе её окна:
    xfade «съедает» только добавленный запас, а не время сцены.
    """
    raw = [max(0.05, float(x)) for x in slices]
    n = len(raw)
    if n < 2:
        return raw
    from .transitions import choose_transition

    try:
        allowed = str(getattr(settings, "allowed_transitions", "hard_only") or "hard_only")
    except Exception:
        allowed = "hard_only"
    if allowed == "hard_only":
        return raw
    intensity = 0.6
    padded = list(raw)
    for _ in range(4):
        overlaps: list[float] = []
        for i in range(n - 1):
            role_a = "intro" if i == 0 else ("outro" if i == n - 1 else "body")
            role_b = "intro" if i + 1 == 0 else ("outro" if i + 1 == n - 1 else "body")
            spec = choose_transition(
                allowed, intensity=intensity,
                role_a=role_a, role_b=role_b, scene_index=i,
            )
            if spec.kind == "hard" or spec.duration <= 0.01:
                overlaps.append(0.0)
            else:
                overlaps.append(min(
                    float(spec.duration),
                    padded[i] * 0.35,
                    padded[i + 1] * 0.35,
                ))
        new = [
            raw[i] + (overlaps[i] if i < n - 1 else 0.0)
            for i in range(n)
        ]
        if all(abs(new[i] - padded[i]) < 1e-4 for i in range(n)):
            padded = new
            break
        padded = new
    return padded


def _scene_bound_shots(
    paths: Sequence[str],
    windows: Sequence[tuple[float, float]],
    clip_durations: Sequence[float] | None = None,
    scene_preferred: dict[int, list[str]] | None = None,
    scene_texts: Sequence[str] | None = None,
) -> tuple[list[str], list[float], list[float], list[int]] | None:
    """Жёсткая привязка: клипы папки сцены N → окно сцены N.

    Для каждой сцены берём ЛУЧШИЙ доступный клип: сначала `_fix`-папку
    (её создаёт fix_scenes для слабых сцен), затем порядок из манифеста
    (лучшие кандидаты), затем остальные. Один клип закрывает окно; если он
    короче — повторяется со сдвигом внутри ЭТОЙ ЖЕ сцены.
    Возвращает (paths, slices, source_offsets, scene_idx).
    """
    if not paths or len(windows) < 2:
        return None
    try:
        from video_maker.semantic.matcher import scene_index_from_path
    except Exception:
        return None
    import os as _os

    by_scene: dict[int, list[int]] = {}
    unbound: list[int] = []
    for i, p in enumerate(paths):
        idx = scene_index_from_path(p)
        if idx is None:
            unbound.append(i)
        else:
            by_scene.setdefault(idx, []).append(i)

    def _rank(pi: int, scene_idx: int) -> tuple | None:
        p = paths[pi]
        folder = str(Path(p).parent.name).lower()
        is_fix = 0 if folder.endswith("_fix") else 1
        pref = (scene_preferred or {}).get(scene_idx) or []
        pos = 10 ** 6
        try:
            rp = _os.path.realpath(p)
            for k, q in enumerate(pref):
                if _os.path.realpath(str(q)) == rp:
                    pos = k
                    break
        except Exception:
            pass
        # Кандидаты сцены: ПЕРВЫЙ клип манифеста (осознанный выбор пайплайна)
        # и _fix-клип (если fix_scenes его добавил). Остальные файлы папки —
        # часто отклонённые/слабые кандидаты, их в кадр не пускаем.
        if pref and not (is_fix == 0 or pos == 0):
            return None
        # Смысловой скор по тексту ИМЕННО этой сцены: при равном скоре
        # выигрывает _fix-папка, затем порядок манифеста.
        sc_score = 0.0
        if scene_texts and 0 <= scene_idx - 1 < len(scene_texts):
            try:
                from video_maker.semantic.matcher import score_clip_vs_text
                sc_score = float(score_clip_vs_text(p, scene_texts[scene_idx - 1]))
            except Exception:
                sc_score = 0.0
        return (-sc_score, is_fix, pos, p)

    for _idx in list(by_scene):
        _ranked = []
        for _pi in by_scene[_idx]:
            _r = _rank(_pi, _idx)
            if _r is not None:
                _ranked.append((_r, _pi))
        if _ranked:
            _ranked.sort(key=lambda t: t[0])
            by_scene[_idx] = [pi for _, pi in _ranked]
        else:
            # preferred-файл не попал в выборку — не оставляем сцену пустой
            by_scene[_idx] = sorted(by_scene[_idx], key=lambda pi: paths[pi])
    # Нужен именно сценный пул: минимум 2 папки, и хотя бы одна попадает в окна.
    if len(by_scene) < 2:
        return None
    if not any((idx - 1) < len(windows) for idx in by_scene):
        return None

    def _avail(pi: int) -> float:
        if clip_durations and pi < len(clip_durations):
            try:
                return max(0.05, float(clip_durations[pi]))
            except (TypeError, ValueError):
                pass
        return 30.0

    out_paths: list[str] = []
    out_slices: list[float] = []
    out_offsets: list[float] = []
    out_scene: list[int] = []

    for wi, (ws, we) in enumerate(windows):
        # Один лучший клип на сцену: остальные клипы папки могут быть
        # слабыми/отклонёнными кандидатами и только размывают соответствие.
        idxs = list(by_scene.get(wi + 1) or [])[:1]
        if not idxs:
            # Сцена без клипов: добираем нераспределённым клипом, не ломая окно.
            if unbound:
                idxs = [unbound.pop(0)]
            else:
                idxs = [wi % len(paths)]
        width = max(0.05, float(we) - float(ws))
        k = len(idxs)
        share = width / k
        remaining = width
        for pos, pi in enumerate(idxs):
            p = paths[pi]
            avail = _avail(pi)
            d = remaining if pos == k - 1 else min(share, remaining)
            d = max(0.05, min(d, avail, remaining))
            out_paths.append(p)
            out_slices.append(d)
            out_offsets.append(0.0)
            out_scene.append(wi)
            remaining -= d
        # Короткие клипы: добираем остаток окна повтором клипов этой сцены.
        guard = 0
        while remaining > 0.05 and guard < 60:
            guard += 1
            for pi in idxs:
                if remaining <= 0.05:
                    break
                avail = _avail(pi)
                d = max(0.05, min(remaining, 2.0, avail))
                out_paths.append(paths[pi])
                out_slices.append(d)
                out_offsets.append(0.0)
                out_scene.append(wi)
                remaining -= d
    if not out_paths:
        return None
    return out_paths, out_slices, out_offsets, out_scene


class PlanBuilder:
    """Builds a validated EditPlan from already-available pipeline data."""

    def __init__(self, settings: CinematicSettings):
        self.settings = settings

    def build(
        self,
        *,
        transcript_segments: Sequence[dict[str, Any]] | None = None,
        broll_clips: Sequence[dict[str, Any]] | None = None,
        analysis: dict[str, Any] | None = None,
        audio_duration: float = 0.0,
        source_audio_hash: str = "",
        output_format: str = "16:9",
    ) -> EditPlan:
        """
        transcript_segments: list of {start, end, text, ...} from existing TranscribeStage
        broll_clips: list of {path, start, end, asset_id, ...} from existing mapping
        analysis: GeminiStage result (topics, highlights, etc.) — optional
        """
        settings = self.settings
        if not settings.enabled:
            return EditPlan(
                source_audio_hash=source_audio_hash,
                settings_hash=_hash_obj(settings.to_dict()),
                total_duration=audio_duration,
                created_at=datetime.now(timezone.utc).isoformat(),
                warnings=["Cinematic Enhancement disabled — empty plan"],
            )

        scenes: list[Scene] = []
        clips = list(broll_clips or [])
        segs = list(transcript_segments or [])

        # Optional light re-ranking when clips carry tags/description
        # or analysis provides topics/keywords (still lexical — not neural).
        analysis_extra = _analysis_terms(analysis)
        if clips and (segs or analysis_extra):
            joined = " ".join(str(s.get("text") or "") for s in segs)
            n_clips = max(1, len(clips))
            avg_slot = (audio_duration / n_clips) if audio_duration > 0 else None
            scored = [
                (
                    score_clip_against_text(
                        c, joined, extra_terms=analysis_extra, desired_duration=avg_slot
                    ),
                    i,
                    c,
                )
                for i, c in enumerate(clips)
            ]
            if any(s > 0 for s, _, _ in scored):
                scored.sort(key=lambda t: (-t[0], t[1]))
                # Diversity: avoid stacking same stem path back-to-back when scores close
                ordered: list = []
                used_stems: list[str] = []
                for sc, i, c in scored:
                    stem = Path(str(c.get("path") or c.get("source") or "")).stem.lower()[:24]
                    if ordered and used_stems and stem and stem == used_stems[-1] and sc < 0.85:
                        continue  # defer duplicate stem
                    ordered.append(c)
                    used_stems.append(stem)
                # append deferred
                for sc, i, c in scored:
                    if c not in ordered:
                        ordered.append(c)
                clips = ordered

        # Fallback: if no B-roll mapping yet, create one static scene per major segment
        if not clips and segs:
            for i, seg in enumerate(segs):
                start = float(seg.get("start", 0))
                end = float(seg.get("end", start + 3))
                text = (seg.get("text") or "")[:80]
                scenes.append(
                    Scene(
                        scene_id=_stable_id("seg", str(i), text),
                        asset_id=f"fallback_{i}",
                        source_path="",  # filled later by integrator
                        source_in=0.0,
                        source_out=end - start,
                        timeline_start=start,
                        timeline_end=end,
                        role="body",
                        transcript_ref=text,
                        output_format=output_format,  # type: ignore
                        crop=CropRect(),
                        motion=self._default_motion(end - start, index=i, role="body"),
                        color_profile=settings.profile,
                        reason="fallback from transcript segment",
                        confidence=0.4,
                        warnings=["No B-roll clip bound — using placeholder"],
                    )
                )
        else:
            # Prefer existing clip mapping
            for i, clip in enumerate(clips):
                path = str(clip.get("path") or clip.get("source") or "")
                asset = str(clip.get("asset_id") or clip.get("id") or f"clip_{i}")
                src_in = float(clip.get("source_in", clip.get("start", 0)))
                src_out = float(clip.get("source_out", clip.get("end", src_in + 4)))
                tl_start = float(clip.get("timeline_start", clip.get("tl_start", src_in)))
                tl_end = float(clip.get("timeline_end", clip.get("tl_end", src_out)))
                role = str(clip.get("role", "broll"))
                conf = float(clip.get("confidence", 0.7))
                seg_text = ""
                if segs:
                    si = min(i, len(segs) - 1)
                    seg_text = str(segs[si].get("text", ""))
                lex = score_clip_against_text(clip, seg_text) if seg_text else 0.0
                if lex > 0:
                    conf = min(1.0, max(conf, 0.4 + 0.5 * lex))
                reason = str(clip.get("reason", "from existing B-roll mapping"))
                if lex > 0:
                    reason = f"{reason}; lexical_score={lex:.2f}"

                scenes.append(
                    Scene(
                        scene_id=_stable_id(asset, str(src_in), str(src_out)),
                        asset_id=asset,
                        source_path=path,
                        source_in=src_in,
                        source_out=src_out,
                        timeline_start=tl_start,
                        timeline_end=tl_end,
                        role=role if role in ("intro", "hook", "body", "emphasis", "transition", "outro", "cta", "broll") else "broll",  # type: ignore
                        transcript_ref=(str(clip.get("text", "")) or seg_text)[:120] or None,
                        output_format=output_format,  # type: ignore
                        crop=self._safe_crop(clip, i),
                        motion=self._default_motion(tl_end - tl_start, index=i, role=role),
                        color_profile=settings.profile,
                        reason=reason,
                        confidence=conf,
                    )
                )

        # Apply tempo scaling (without changing audio)
        if settings.plan_tempo != 1.0 and scenes:
            # Only scale visual dwell if source allows; audio stays untouched
            pass  # real scaling of visual holds done in CE-03/04

        # Bias pan toward detected face/subject ROI
        for _sc in scenes:
            _sc.motion = self._bias_motion_to_crop(_sc.motion, _sc.crop)

        plan = EditPlan(
            source_audio_hash=source_audio_hash,
            settings_hash=_hash_obj(settings.to_dict()),
            scenes=scenes,
            total_duration=audio_duration or (scenes[-1].timeline_end if scenes else 0.0),
            created_at=datetime.now(timezone.utc).isoformat(),
            algorithm_version=settings.algorithm_version,
        )
        errs = plan.validate()
        if errs:
            plan.warnings.extend(errs)
        return plan

    def _default_motion(self, duration: float, *, index: int = 0, role: str = "body") -> list[MotionKeyframe]:
        """Deterministic cinematic motion variety.

        Patterns alternate so consecutive scenes rarely share the same move.
        Never exceeds max_zoom. Short clips stay static.
        """
        intens = self.settings.motion_intensity
        if intens == "off" or duration < 1.5:
            return [MotionKeyframe(t=0.0, scale=1.0), MotionKeyframe(t=1.0, scale=1.0)]
        max_s = min(
            self.settings.max_zoom,
            1.12 if intens == "low" else 1.20 if intens == "medium" else 1.30,
        )
        # Avoid identical motion on neighbours: use seed + index + role weight
        role_w = {"intro": 1, "hook": 2, "emphasis": 3, "outro": 4, "cta": 4}.get(role, 0)
        pattern = (self.settings.seed + index * 3 + role_w) % 9
        if role in ("outro", "cta"):
            pattern = 3  # calm hold on endings
        if role == "hook":
            pattern = pattern % 4  # prefer active moves on hooks

        if pattern == 0:
            # classic slow zoom-in
            return [
                MotionKeyframe(t=0.0, scale=1.0, easing="ease_in_out"),
                MotionKeyframe(t=1.0, scale=max_s, easing="ease_in_out"),
            ]
        if pattern == 1:
            # zoom-out (reveal)
            return [
                MotionKeyframe(t=0.0, scale=max_s, easing="ease_in_out"),
                MotionKeyframe(t=1.0, scale=1.0, easing="ease_in_out"),
            ]
        if pattern == 2:
            # horizontal pan with slight zoom
            return [
                MotionKeyframe(t=0.0, scale=1.05, pan_x=-0.30, pan_y=0.0, easing="ease_in_out"),
                MotionKeyframe(t=1.0, scale=1.05, pan_x=0.30, pan_y=0.0, easing="ease_in_out"),
            ]
        if pattern == 3:
            # static / breathing room
            hold = 1.0 if intens == "low" else min(1.04, max_s)
            return [
                MotionKeyframe(t=0.0, scale=hold, easing="linear"),
                MotionKeyframe(t=1.0, scale=hold, easing="linear"),
            ]
        if pattern == 4:
            # vertical drift + zoom-in
            return [
                MotionKeyframe(t=0.0, scale=1.0, pan_x=0.0, pan_y=-0.20, easing="ease_in_out"),
                MotionKeyframe(t=1.0, scale=max_s, pan_x=0.0, pan_y=0.20, easing="ease_in_out"),
            ]
        if pattern == 5:
            # diagonal push-in
            return [
                MotionKeyframe(t=0.0, scale=1.0, pan_x=-0.14, pan_y=-0.10, easing="ease_out"),
                MotionKeyframe(t=1.0, scale=max_s, pan_x=0.14, pan_y=0.10, easing="ease_in"),
            ]
        if pattern == 6:
            # hold then late zoom-in (tension)
            return [
                MotionKeyframe(t=0.0, scale=1.0, easing="linear"),
                MotionKeyframe(t=0.55, scale=1.02, easing="ease_in"),
                MotionKeyframe(t=1.0, scale=max_s, easing="ease_out"),
            ]
        if pattern == 7:
            # reverse diagonal (pull-back reveal)
            return [
                MotionKeyframe(t=0.0, scale=max_s, pan_x=0.14, pan_y=-0.08, easing="ease_in_out"),
                MotionKeyframe(t=1.0, scale=1.0, pan_x=-0.10, pan_y=0.08, easing="ease_in_out"),
            ]
        # pattern 8: gentle arc (pan + mild zoom, documentary feel)
        return [
            MotionKeyframe(t=0.0, scale=1.02, pan_x=-0.18, pan_y=0.06, easing="ease_in_out"),
            MotionKeyframe(t=0.5, scale=1.0 + (max_s - 1.0) * 0.5, pan_x=0.0, pan_y=0.0, easing="ease_in_out"),
            MotionKeyframe(t=1.0, scale=max_s * 0.98, pan_x=0.18, pan_y=-0.06, easing="ease_in_out"),
        ]


    def _bias_motion_to_crop(self, motion: list, crop) -> list:
        """Shift pan so zoom drifts toward subject (crop center) not empty edge."""
        if not motion or crop is None:
            return motion
        try:
            if float(crop.w) >= 0.98 and float(crop.h) >= 0.98:
                return motion  # full frame — no subject bias
            # Crop center relative to frame center, mapped to pan range ~[-0.4,0.4]
            cx = float(crop.x) + float(crop.w) / 2.0
            cy = float(crop.y) + float(crop.h) / 2.0
            bias_x = max(-0.35, min(0.35, (cx - 0.5) * 1.2))
            bias_y = max(-0.35, min(0.35, (cy - 0.5) * 1.2))
            out = []
            for k in motion:
                from .edit_plan import MotionKeyframe
                out.append(MotionKeyframe(
                    t=k.t,
                    scale=k.scale,
                    pan_x=max(-1.0, min(1.0, float(k.pan_x) + bias_x * 0.5)),
                    pan_y=max(-1.0, min(1.0, float(k.pan_y) + bias_y * 0.5)),
                    easing=getattr(k, "easing", "ease_in_out") or "ease_in_out",
                ))
            return out
        except Exception:
            return motion

    def _safe_crop(self, clip: dict[str, Any], scene_index: int = 0) -> CropRect:
        """
        framing_mode:
          center   → full frame
          safe_roi → provided ROI, else face/skin estimate, else mild center crop
          manual   → provided ROI/crop only; else full frame (no auto guess)
        """
        mode = self.settings.framing_mode
        if mode == "center":
            return CropRect()
        roi = clip.get("roi") or clip.get("crop")
        if isinstance(roi, dict):
            try:
                c = CropRect(**{k: float(roi[k]) for k in ("x", "y", "w", "h") if k in roi})
                if not c.validate():
                    return c
            except Exception:
                pass
        if mode == "manual":
            return CropRect()
        # safe_roi: try lightweight detector (Haar if cv2, else skin/center)
        path = str(clip.get("path") or clip.get("source") or "")
        if path:
            try:
                from .roi_detect import estimate_roi
                est = estimate_roi(path, clip=clip, t_sec=0.4 + 0.1 * (scene_index % 5))
                if est.w < 0.99:  # non-full frame means we found something
                    # [VM-FIX] не резать кадр агрессивно: кроп не меньше 70% —
                    # иначе клип «увеличивается» в разы (детектор давал 28-42%).
                    w = max(0.7, min(1.0, est.w))
                    h = max(0.7, min(1.0, est.h))
                    cx = est.x + est.w / 2.0
                    cy = est.y + est.h / 2.0
                    x = max(0.0, min(1.0 - w, cx - w / 2.0))
                    y = max(0.0, min(1.0 - h, cy - h / 2.0))
                    return CropRect(x=x, y=y, w=w, h=h)
            except Exception:
                pass
        # deterministic mild crop fallback
        import hashlib
        h = hashlib.md5(
            f"{self.settings.seed}:{scene_index}:{clip.get('path', '')}".encode()
        ).hexdigest()
        bias = int(h[:2], 16) / 255.0
        inset = 0.08 + 0.04 * (int(h[2:4], 16) / 255.0)
        w = hgt = 1.0 - 2 * inset
        max_x = max(0.0, 1.0 - w)
        max_y = max(0.0, 1.0 - hgt)
        x = max_x * (0.35 + 0.30 * bias)
        y = max_y * 0.45
        c = CropRect(x=x, y=y, w=w, h=hgt)
        return c if not c.validate() else CropRect()

    def build_from_broll(
        self,
        paths: list[str],
        *,
        target_duration: float,
        transcript_segments: Sequence[dict[str, Any]] | None = None,
        output_format: str = "16:9",
        weights: list[float] | None = None,
        clip_durations: list[float] | None = None,
        scene_bound: bool = False,
        scene_preferred: dict[int, list[str]] | None = None,
    ) -> EditPlan:
        """Plan from ordered file paths (final selected list).

        scene_bound=True — смарт-пул разложен по папкам сцен (01_, 02_, …):
        клипы сцены N жёстко ставятся в окно сцены N, без смысловой
        перетасовки и пословных перерезаний (они уводят клип из своей сцены).
        """
        n = max(1, len(paths))
        weights = weights or [1.0 / n] * n
        segs = list(transcript_segments or [])

        # [ORD] Жёсткая привязка scene→slots для сценного пула.
        _bound: tuple | None = None
        _bound_slices: list[float] = []
        _bound_offsets: list[float] = []
        _bound_scene: list[int] = []
        _ord_note = ""
        if scene_bound and segs:
            _windows = _scene_windows_from_segments(segs, float(target_duration or 0.0))
            _bound = _scene_bound_shots(
                paths, _windows, clip_durations,
                scene_preferred=scene_preferred,
                scene_texts=[str(s.get("text") or "") for s in segs],
            )
            if _bound:
                _dur_by_path: dict[str, float] = {}
                if clip_durations and len(clip_durations) == len(paths):
                    for _p, _d in zip(paths, clip_durations):
                        _dur_by_path[_p] = float(_d)
                paths, _bound_slices, _bound_offsets, _bound_scene = _bound
                clip_durations = [float(_dur_by_path.get(_p, 30.0)) for _p in paths]
                # Запас под xfade: не сдвигает границы сцен (см. helper).
                _bound_slices = _pad_bound_for_transitions(_bound_slices, self.settings)
                n = max(1, len(paths))
                _total_b = sum(_bound_slices) or 1.0
                weights = [d / _total_b for d in _bound_slices]
                _ord_note = (
                    f"[ORD] scene→slots: {len(_bound_slices)} планов, "
                    f"{len(_windows)} окон, {len(set(_bound_scene))} сцен"
                )

        # [VM-SB] По-сценный смысловой подбор («псевдо-нейролинк»).
        # Каждому слоту — лучший неиспользованный клип по тексту ЭТОЙ сцены
        # (пропорциональные окна сегментов). Без совпадений — исходный порядок.
        if _bound is None and n > 1 and segs and getattr(self.settings, "smart_broll", True):
            try:
                from video_maker.semantic.matcher import score_clip_vs_text

                def _sb_slot_text(idx: int) -> str:
                    m = len(segs)
                    s0 = int(idx * m / n)
                    s1 = max(s0 + 1, int((idx + 1) * m / n))
                    return " ".join(str(s.get("text") or "") for s in segs[s0:s1])

                _sb_unused = list(range(n))
                _sb_order: list[int] = []
                _sb_any = False
                for _i in range(n):
                    _best_j, _best_sc = None, 0.0
                    _text = _sb_slot_text(_i)
                    for _j in _sb_unused:
                        _sc = score_clip_vs_text(paths[_j], _text)
                        if _sc > _best_sc:
                            _best_j, _best_sc = _j, _sc
                    if _best_j is None:
                        _best_j = _sb_unused[0]
                    else:
                        _sb_any = True
                    _sb_order.append(_best_j)
                    _sb_unused.remove(_best_j)
                if _sb_any:
                    paths = [paths[_j] for _j in _sb_order]
                    weights = [weights[_j] for _j in _sb_order]
                    if clip_durations and len(clip_durations) == n:
                        clip_durations = [clip_durations[_j] for _j in _sb_order]
            except Exception:
                pass

        # [VM-SB] Пословный монтаж: смена кадра под ключевые слова озвучки.
        # «переписываешься» → кадр про переписку; «выпивают» → кадр с компанией.
        # Точки реза — старты значимых слов, не чаще min_shot_sec; клип
        # подбирается под слова каждого окна (повторы — со сдвигом источника).
        _kw_clips: list[str] = []
        _kw_slices: list[float] = []
        _kw_offsets: list[float] = []
        try:
            _kw_on = bool(getattr(self.settings, "keyword_cuts", False))
        except Exception:
            _kw_on = False
        if _bound is None and _kw_on and segs and paths and float(target_duration or 0) > 1.0:
            try:
                _min_shot = float(getattr(self.settings, "min_shot_sec", 2.0) or 2.0)
            except Exception:
                _min_shot = 2.0
            _min_shot = min(6.0, max(1.2, _min_shot))
            _words: list[tuple[float, float, str]] = []
            for _s in segs:
                _ws_list = _s.get("words")
                if isinstance(_ws_list, list) and _ws_list:
                    for _w in _ws_list:
                        try:
                            _ws = float(_w.get("start", _s.get("start", 0.0)))
                            _we = float(_w.get("end", _ws + 0.3))
                            _wt = str(_w.get("word") or _w.get("text") or "").strip()
                            if _wt:
                                _words.append((_ws, _we, _wt))
                        except Exception:
                            continue
            if _words:
                _words.sort(key=lambda t: t[0])
                from video_maker.semantic.taxonomy import theme_for_token as _theme
                _cuts = [0.0]
                _last = 0.0
                _max_shot = max(3.0, _min_shot * 2.5)
                for _ws, _we, _wt in _words:
                    if _ws - _last < _min_shot or _ws >= float(target_duration) - 0.6:
                        continue
                    _tok = "".join(ch for ch in _wt.lower() if ch.isalpha() or ch in "ё-")
                    _is_theme = bool(_tok) and _theme(_tok) is not None
                    # рез на ключевом слове ИЛИ принудительно каждые max_shot
                    if not _is_theme and (_ws - _last) < _max_shot:
                        continue
                    _cuts.append(_ws)
                    _last = _ws
                _cuts.append(float(target_duration))
                if len(_cuts) >= 3:
                    from video_maker.semantic.matcher import score_clip_vs_text as _score
                    _avail_map: dict[str, float] = {}
                    if clip_durations and len(clip_durations) == len(paths):
                        for _i, _p in enumerate(paths):
                            _avail_map[_p] = max(0.05, float(clip_durations[_i]))
                    _wins = []
                    for _i in range(len(_cuts) - 1):
                        _a, _b = _cuts[_i], _cuts[_i + 1]
                        _dur = max(0.4, _b - _a)
                        _text = " ".join(w for ws2, we2, w in _words if _a - 0.01 <= ws2 < _b - 0.01)
                        _wins.append({"a": _a, "dur": _dur, "text": _text})
                    _unused = list(paths)
                    _assign: list = [None] * len(_wins)
                    # Фаза 1: окна со смысловым совпадением — первыми (сильные вперёд),
                    # чтобы окно без ключевых слов не «украло» нужный клип.
                    _scored = []
                    for _i, _w in enumerate(_wins):
                        _best, _best_sc = None, 0.0
                        for _c in _unused:
                            _av = _avail_map.get(_c)
                            if _av is not None and _av < _w["dur"] - 0.05:
                                continue
                            _sc = _score(_c, _w["text"])
                            if _sc > _best_sc:
                                _best, _best_sc = _c, _sc
                        if _best is not None:
                            _scored.append((_best_sc, _i, _best))
                    _scored.sort(key=lambda t: -t[0])
                    for _sc, _i, _c in _scored:
                        if _assign[_i] is None and _c in _unused:
                            _assign[_i] = _c
                            _unused.remove(_c)
                    # Фаза 2: остальные окна — свободные клипы по порядку
                    for _i, _w in enumerate(_wins):
                        if _assign[_i] is not None:
                            continue
                        _cand = None
                        for _c in _unused:
                            _av = _avail_map.get(_c)
                            if _av is not None and _av < _w["dur"] - 0.05:
                                continue
                            _cand = _c
                            break
                        if _cand is None and _unused:
                            _cand = _unused[0]
                        if _cand is None:
                            _cand = paths[_i % len(paths)]
                        _assign[_i] = _cand
                        if _cand in _unused:
                            _unused.remove(_cand)
                    _use_count: dict[str, int] = {}
                    for _i, _w in enumerate(_wins):
                        _c = _assign[_i] or paths[_i % len(paths)]
                        _n_used = _use_count.get(_c, 0)
                        _use_count[_c] = _n_used + 1
                        _kw_clips.append(_c)
                        _kw_slices.append(_w["dur"])
                        _kw_offsets.append(_n_used * _w["dur"])  # повтор — со сдвигом
        if _kw_clips:
            # [VM-FIX] clip_durations выравниваем с НОВЫМ порядком клипов:
            # иначе avail берётся от чужого файла → trim за концом клипа →
            # пустой вход → обрыв цепочки xfade (видео короче озвучки).
            if clip_durations and len(clip_durations) == len(paths):
                _dur_by_path: dict[str, float] = {}
                for _p, _d in zip(paths, clip_durations):
                    _dur_by_path[_p] = float(_d)
                clip_durations = [float(_dur_by_path.get(_p, 0.05)) for _p in _kw_clips]
            paths = _kw_clips
            _total_kw = sum(_kw_slices) or 1.0
            weights = [d / _total_kw for d in _kw_slices]

        # [VM-SB] Темп планов = «потолок длины плана» (аудио не меняется).
        # План не длиннее 9с/темп: темп↑ → короче планы (больше склеек и
        # разнообразия), темп↓ → длиннее и спокойнее. Суммарная длительность
        # сохраняется: излишек от «упёршихся в потолок» клипов перераспределяется.
        _slices: list[float] = []
        try:
            _tempo = float(getattr(self.settings, "plan_tempo", 1.0) or 1.0)
        except Exception:
            _tempo = 1.0
        # [VM-FIX] Переходы (xfade) «съедают» время: мягкие переходы по ~0.47с
        # на каждый стык. Слоты должны суммарно покрывать target + Σпереходов,
        # иначе видео короче озвучки и замирает на последнем кадре.
        _trans = str(getattr(self.settings, "allowed_transitions", "hard_only") or "hard_only")
        _xfade_est = 0.0 if _trans == "hard_only" else 0.47 * max(0, len(paths) - 1)
        _fill_target = float(target_duration) + _xfade_est
        if _bound is None and not _kw_clips and paths and clip_durations and len(clip_durations) == len(paths):
            _cap = max(4.0, 9.0 / max(0.5, min(2.0, _tempo)))
            _avails = [max(0.05, float(d)) for d in clip_durations]
            _caps = [min(a, _cap) for a in _avails]
            if sum(_caps) >= _fill_target * 0.98:
                _slices = [
                    _fill_target * (weights[i] if i < len(weights) else 1.0 / len(paths))
                    for i in range(len(paths))
                ]
                for _ in range(6):  # перераспределение излишка с «упёршихся» планов
                    _over = 0.0
                    _active = 0.0
                    for _i in range(len(_slices)):
                        if _slices[_i] > _caps[_i] + 1e-6:
                            _over += _slices[_i] - _caps[_i]
                            _slices[_i] = _caps[_i]
                        else:
                            _active += _slices[_i]
                    if _over < 1e-3 or _active <= 1e-6:
                        break
                    for _i in range(len(_slices)):
                        if _slices[_i] < _caps[_i] - 1e-6:
                            _slices[_i] += _over * (_slices[_i] / _active)
        n = max(1, len(paths))

        scenes: list[Scene] = []
        t_cursor = 0.0
        if _kw_clips:
            _slices = _kw_slices
        elif _bound is not None:
            _slices = _bound_slices
            _kw_offsets = _bound_offsets
        for i, path in enumerate(paths):
            w = weights[i] if i < len(weights) else 1.0 / n
            slice_dur = _slices[i] if i < len(_slices) else max(0.05, _fill_target * w)
            slice_dur = max(0.05, slice_dur)
            _si = _kw_offsets[i] if i < len(_kw_offsets) else 0.0
            if clip_durations and i < len(clip_durations):
                avail = max(0.05, float(clip_durations[i]))
                slice_dur = min(slice_dur, avail)
                _si = min(_si, max(0.0, avail - slice_dur))
            text = ""
            if segs:
                if _bound is not None and i < len(_bound_scene):
                    _seg_i = min(_bound_scene[i], len(segs) - 1)
                    text = str(segs[_seg_i].get("text") or "")[:120]
                else:
                    # [VM-SB] пропорциональное окно сегментов (точнее, чем min(i, len-1))
                    m = len(segs)
                    s0 = int(i * m / n)
                    s1 = max(s0 + 1, int((i + 1) * m / n))
                    text = " ".join(str(s.get("text") or "") for s in segs[s0:s1])[:120]
            role = "body"
            if i == 0:
                role = "intro"
            elif i == n - 1:
                role = "outro"
            scenes.append(
                Scene(
                    scene_id=_stable_id("broll", str(i), path),
                    asset_id=f"clip_{i}",
                    source_path=path,
                    source_in=_si,
                    source_out=_si + slice_dur,
                    timeline_start=t_cursor,
                    timeline_end=t_cursor + slice_dur,
                    role=role,  # type: ignore
                    transcript_ref=text or None,
                    output_format=output_format,  # type: ignore
                    crop=self._safe_crop({"path": path}, i),
                    motion=self._default_motion(slice_dur, index=i, role=role),
                    color_profile=self.settings.profile,
                    reason=("scene-bound pool order" if _bound is not None
                            else "selected B-roll order"),
                    confidence=0.5,
                )
            )
            t_cursor += slice_dur
        # [VM-FIX] Контент плана обязан покрывать озвучку с запасом на переходы
        # (xfade съедает время на стыках): иначе видео короче аудио и в финале
        # последний кадр замирает. Излишек apply_rhythm сожмёт точно под озвучку.
        # hard_only (только жёсткие склейки) — запас не нужен.
        try:
            _trans_mode = str(getattr(self.settings, "allowed_transitions", "hard_only") or "hard_only")
        except Exception:
            _trans_mode = "hard_only"
        try:
            _xf_all = 0.47 * max(0, len(scenes) - 1) if _trans_mode != "hard_only" else 0.0
            _need_all = float(target_duration or 0.0) + _xf_all
            _tail = 0
            # [ORD] В сценном режиме хвост не добираем: он поставил бы клипы
            # сцен 01/02 после последней сцены. Покрытие уже задано окнами.
            while _bound is None and scenes and t_cursor < _need_all and _tail < 600:
                _i2 = _tail % len(paths)
                _p = paths[_i2]
                _av = 4.0
                if clip_durations and _i2 < len(clip_durations):
                    try:
                        _av = max(0.4, float(clip_durations[_i2]))
                    except Exception:
                        _av = 4.0
                _d = max(0.4, min(3.5, _av))
                scenes.append(
                    Scene(
                        scene_id=_stable_id("broll_tail", str(_tail), _p),
                        asset_id=f"clip_tail_{_tail}",
                        source_path=_p,
                        source_in=0.0,
                        source_out=_d,
                        timeline_start=t_cursor,
                        timeline_end=t_cursor + _d,
                        role="body",
                        transcript_ref=None,
                        output_format=output_format,  # type: ignore
                        crop=self._safe_crop({"path": _p}, len(scenes)),
                        motion=self._default_motion(_d, index=len(scenes), role="body"),
                        color_profile=self.settings.profile,
                        reason="tail fill to cover narration",
                        confidence=0.4,
                    )
                )
                t_cursor += _d
                _tail += 1
        except Exception:
            pass
        _plan = EditPlan(
            source_audio_hash="",
            settings_hash=_hash_obj(self.settings.to_dict()),
            scenes=scenes,
            total_duration=target_duration or t_cursor,
            created_at=datetime.now(timezone.utc).isoformat(),
            algorithm_version=self.settings.algorithm_version,
        )
        if _ord_note:
            _plan.warnings.append(_ord_note)
        return _plan
