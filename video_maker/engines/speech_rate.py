"""Speech-rate analysis and pitch-preserving narration preparation for VideoMaker.

Uses the MLX Whisper transcript shape (segments[].text and segments[].words[]).
Internal pause compression is optional and conservative: by default, every
internal pause remains unchanged. When enabled, only gaps above a threshold are
shortened; phrase/sentence pauses retain a longer minimum than ordinary gaps.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import math
import os
import re
import subprocess
import time
from typing import Any, Callable

from ..paths import resolve_existing_file

_WORD_RE = re.compile(r"(?u)[^\W_]+(?:[-'’][^\W_]+)*")
_RUBBERBAND_CACHE: dict[str, bool] = {}

DEFAULT_MIN_WPM = 120.0
DEFAULT_MAX_WPM = 150.0
DEFAULT_TARGET_WPM = 120.0
DEFAULT_LEAD_PAD_SECONDS = 0.12
DEFAULT_TAIL_PAD_SECONDS = 0.22
DEFAULT_PAUSE_THRESHOLD_SECONDS = 0.55
DEFAULT_PAUSE_KEEP_SECONDS = 0.18
DEFAULT_CLAUSE_PAUSE_KEEP_SECONDS = 0.28
DEFAULT_SENTENCE_PAUSE_KEEP_SECONDS = 0.38

# r57-api.22: ползунок скорости речи — режимы и состояние последней подготовки
SPEECH_STATE_PATH = os.path.join(os.path.expanduser("~"), "video_maker", "cache", "speech_state.json")
SPEED_MODE_WPM = "wpm"
SPEED_MODE_COEFFICIENT = "coefficient"
SPEED_MODE_LABELS = {
    SPEED_MODE_WPM: "По словам/мин",
    SPEED_MODE_COEFFICIENT: "По коэффициенту",
}
# r57-api.25: скорость шортов — третий режим «как на длинном» (по умолчанию).
SPEED_MODE_INHERIT = "inherit"
SHORT_SPEED_MODE_LABELS = {
    SPEED_MODE_INHERIT: "Как на длинном",
    SPEED_MODE_WPM: "По словам/мин",
    SPEED_MODE_COEFFICIENT: "По коэффициенту",
}

LogFn = Callable[[str], Any]


def count_words(text: str | None) -> int:
    """Count lexical tokens, ignoring punctuation and whitespace.

    Hyphenated compounds such as ``кто-то`` count as one word. A bare punctuation
    mark contributes zero. Numeric tokens are counted as words, which is useful
    for transcripts where Whisper renders spoken numbers as digits.
    """
    return len(_WORD_RE.findall(text or ""))


def transcription_text(transcription: dict[str, Any] | None) -> str:
    """Return the transcript text, with a safe fallback to segment text."""
    if not isinstance(transcription, dict):
        return ""
    text = str(transcription.get("text") or "").strip()
    if text:
        return text
    segments = transcription.get("segments") or []
    return " ".join(
        str(s.get("text") or "").strip()
        for s in segments
        if isinstance(s, dict) and str(s.get("text") or "").strip()
    ).strip()


def _is_finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def timed_words(transcription: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Flatten valid lexical word timestamps from MLX Whisper transcription."""
    out: list[dict[str, Any]] = []
    if not isinstance(transcription, dict):
        return out
    for segment in transcription.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        for word in segment.get("words") or []:
            if not isinstance(word, dict):
                continue
            token = str(word.get("word") or word.get("text") or "").strip()
            if count_words(token) == 0:
                continue
            start, end = word.get("start"), word.get("end")
            if not (_is_finite_number(start) and _is_finite_number(end)):
                continue
            start_f, end_f = float(start), float(end)
            if start_f < 0 or end_f <= start_f:
                continue
            out.append({"start": start_f, "end": end_f, "token": token})
    return out


def _normalized_lexical_text(text: str) -> str:
    """Только буквы/цифры в нижнем регистре (ё→е), без пунктуации и дефисов.

    Нужно для сверки текста и токенов: Whisper режет дефисные слова на два
    токена («Что» + «-то»), а счётчики слов считают их одним — по счётчикам
    покрытие ошибочно выглядело неполным.
    """
    value = str(text or "").lower().replace("ё", "е")
    return "".join(ch for ch in value if ch.isalnum())


def _timed_words_match_transcript(transcription: dict[str, Any] | None, words: list[dict[str, Any]]) -> bool:
    """Return whether word-level timestamps cover the transcript's lexical words.

    If Whisper supplied only a partial word list, cutting the file from those few
    timestamps would be unsafe. A transcript without segment text can still use
    its valid word list as the best available source.

    Сравниваем нормализованные последовательности букв/цифр, а не счётчики слов:
    дефисные слова («что-то», «когда-либо») приходят двумя токенами, и по
    счётчикам покрытие ложно считалось неполным — трим и удаление пауз молча
    отключались на всей серии.
    """
    transcript_norm = _normalized_lexical_text(transcription_text(transcription))
    if not transcript_norm:
        return True
    timed_norm = "".join(
        _normalized_lexical_text(str(word.get("token") or "")) for word in words
    )
    return transcript_norm == timed_norm


def _hyphen_merged_timed_words(transcription: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Слова с таймкодами, склеивая дефисные хвосты Whisper («кое» + «-что»)."""
    out: list[dict[str, Any]] = []
    for word in timed_words(transcription):
        token = str(word.get("token") or "")
        if token.startswith("-") and out:
            out[-1]["token"] = str(out[-1]["token"]) + token
            out[-1]["end"] = float(word["end"])
            continue
        out.append({"token": token, "start": float(word["start"]), "end": float(word["end"])})
    return out


def find_extra_word_intervals(
    transcription: dict[str, Any] | None,
    corrected_text: str | None,
    *,
    max_run_words: int = 8,
    keep_seconds: float = 0.05,
) -> list[tuple[float, float]]:
    """Интервалы слов, которые Whisper слышал, а итоговый текст Gemini не сохранил.

    Сравниваем слова Whisper с corrected_text: «delete» — повторы/оговорки (режем),
    «replace» — исправления распознавания (не трогаем), дефисные слова
    («кое что» ↔ «кое-что») считаются совпадением. Защита: прогоны длиннее
    max_run_words слов не режем — это не оговорка, а потерянный кусок текста.
    """
    text = str(corrected_text or "").strip()
    if not text:
        return []
    words = _hyphen_merged_timed_words(transcription)
    if len(words) < 4:
        return []
    a = [_normalized_lexical_text(w["token"]) for w in words]
    b = [
        _normalized_lexical_text(token)
        for token in re.findall(r"(?u)[^\W_]+(?:[-'’][^\W_]+)*", text)
    ]
    b = [token for token in b if token]
    if not b:
        return []
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    intervals: list[tuple[float, float]] = []
    limit = max(1, int(max_run_words))
    for op, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if op != "delete" or i2 <= i1 or i2 - i1 > limit:
            continue
        start = max(0.0, float(words[i1]["start"]) - float(keep_seconds))
        end = float(words[i2 - 1]["end"]) + float(keep_seconds)
        if end - start > 0.02:
            intervals.append((start, end))
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + 0.005:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def cut_intervals(
    source_path: str,
    output_path: str,
    intervals: list[tuple[float, float]],
    *,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    crossfade_seconds: float = 0.015,
) -> str:
    """Вырезать интервалы из готового аудио с микро-фейдами на каждом стыке.

    Позиции фейдов — в выходном таймлайне (после всех удалений), поэтому стыки
    внутри речи не щёлкают.
    """
    if not intervals:
        return source_path
    duration = _probe_duration(source_path, ffprobe_bin=ffprobe_bin)
    if duration <= 0:
        return source_path
    select = _pause_select_filter(intervals, 0.0)
    if not select:
        return source_path
    fade = max(0.005, min(0.05, float(crossfade_seconds)))
    filters = [select, "asetpts=N/SR/TB"]
    removed = 0.0
    for raw_start, raw_end in sorted(intervals):
        start = max(0.0, min(float(raw_start), duration))
        end = max(start, min(float(raw_end), duration))
        if end - start <= 0.01:
            continue
        seam = max(0.0, start - removed)
        filters.append(f"afade=t=out:st={max(0.0, seam - fade):.6f}:d={fade:.4f}")
        filters.append(f"afade=t=in:st={seam:.6f}:d={fade:.4f}")
        removed += end - start
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    command = [
        ffmpeg_bin, "-hide_banner", "-y", "-i", source_path,
        "-map", "0:a:0", "-vn", "-af", ",".join(filters),
        "-c:a", "pcm_s16le", "-ar", "48000", "-map_metadata", "-1", output_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=3600, check=False)
    if result.returncode != 0 or not os.path.isfile(output_path) or os.path.getsize(output_path) < 1024:
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except OSError:
            pass
        detail = (result.stderr or result.stdout or "FFmpeg failed")[-800:]
        raise RuntimeError(f"Cut intervals failed: {detail}")
    return output_path


def shift_time_after_removals(value: Any, intervals: list[tuple[float, float]]) -> float:
    """Позиция на таймлайне после удаления интервалов (то же место в новом видео)."""
    t = _safe_float(value, 0.0)
    if t <= 0:
        return max(0.0, t)
    removed = 0.0
    for start, end in intervals:
        if t <= start:
            break
        removed += min(t, end) - start
    return max(0.0, t - removed)


def shift_analysis_times(analysis: Any, intervals: list[tuple[float, float]]) -> int:
    """Сдвинуть тайминги анализа Gemini после вырезки интервалов из речи.

    Клипы шортов, хуки, CTA, intro/middle/outro и сильные слова привязаны к
    таймлайну речи. После вырезки повторов видео короче — тайминги обязаны
    следовать, иначе шорты режутся не там, где выбрал Gemini. Возвращает число
    изменённых записей.
    """
    if not isinstance(analysis, dict) or not intervals:
        return 0
    changed = 0

    def _shift_node(node: Any) -> None:
        nonlocal changed
        if isinstance(node, dict):
            hit = False
            for key in ("start", "end", "timing"):
                if key in node:
                    node[key] = shift_time_after_removals(node[key], intervals)
                    hit = True
            if hit:
                changed += 1
        elif isinstance(node, list):
            for item in node:
                _shift_node(item)

    for clip in analysis.get("clips_for_shorts") or []:
        if isinstance(clip, dict):
            hit = False
            for key in ("start", "end", "hook_start", "hook_end", "cta_start", "cta_end"):
                if key in clip:
                    clip[key] = shift_time_after_removals(clip[key], intervals)
                    hit = True
            if hit:
                changed += 1
    for key in (
        "hooks_wide", "hooks_vertical", "cta_wide", "cta_vertical",
        "intro", "middle", "outro", "strong_words",
    ):
        _shift_node(analysis.get(key))
    return changed


def apply_extra_word_cuts(
    ctx: Any,
    corrected_text: str,
    *,
    log_fn: LogFn | None = None,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
) -> int:
    """Вырезать из речи слова, которых нет в тексте Gemini (повторы/оговорки).

    Работает поверх уже подготовленной речи (паузы/темп): интервалы берутся из
    текущих (пересчитанных) таймкодов. Возвращает число вырезанных мест.
    """
    _log = log_fn or (lambda _message: None)
    source = str(getattr(ctx, "narration_path", "") or getattr(ctx, "audio_path", "") or "")
    if not source or not os.path.isfile(source):
        _log("[EXTRA WORDS] пропуск: нет файла речи")
        return 0
    transcription = getattr(ctx, "transcription", None) or {}
    intervals = find_extra_word_intervals(transcription, corrected_text)
    if not intervals:
        _log("[EXTRA WORDS] повторов/лишних слов по тексту Gemini не найдено")
        return 0
    stem = os.path.basename(source).rsplit(".", 1)[0][-12:]
    out_path = os.path.join(os.path.dirname(os.path.abspath(source)), f"narration_no_extra_{stem}.wav")
    old_duration = _probe_duration(source, ffprobe_bin=ffprobe_bin)
    cut_intervals(source, out_path, intervals, ffmpeg_bin=ffmpeg_bin, ffprobe_bin=ffprobe_bin)
    new_duration = _probe_duration(out_path, ffprobe_bin=ffprobe_bin)
    if new_duration <= 0:
        _log("[EXTRA WORDS] пропуск: не удалось измерить результат")
        return 0
    ctx.transcription = retime_transcription(
        transcription,
        trim_start=0.0,
        trim_end=old_duration,
        tempo_factor=1.0,
        final_duration=new_duration,
        removed_intervals=intervals,
    )
    ctx.narration_path = out_path
    try:
        ctx.audio_duration = new_duration
    except Exception:  # noqa: BLE001
        pass
    shifted = shift_analysis_times(getattr(ctx, "analysis", None), intervals)
    if shifted:
        _log(f"[EXTRA WORDS] тайминги анализа Gemini пересчитаны: {shifted} записей")
    metrics = getattr(ctx, "speech_metrics", None)
    removed_seconds = sum(end - start for start, end in intervals)
    if isinstance(metrics, dict):
        metrics["extra_words_intervals_removed"] = len(intervals)
        metrics["extra_words_seconds_removed"] = round(removed_seconds, 4)
        metrics["extra_words_applied"] = True
    _log(
        f"[EXTRA WORDS] вырезано повторов/лишних слов: {len(intervals)} "
        f"({removed_seconds:.2f}с)"
    )
    for start, end in intervals:
        _log(f"[EXTRA WORDS]   {start:.2f}–{end:.2f}с")
    return len(intervals)


def find_long_pause_intervals(
    transcription: dict[str, Any] | None,
    trim_start: float,
    trim_end: float,
    *,
    threshold_seconds: float = DEFAULT_PAUSE_THRESHOLD_SECONDS,
    keep_gap_seconds: float = DEFAULT_PAUSE_KEEP_SECONDS,
    clause_keep_seconds: float = DEFAULT_CLAUSE_PAUSE_KEEP_SECONDS,
    sentence_keep_seconds: float = DEFAULT_SENTENCE_PAUSE_KEEP_SECONDS,
) -> list[tuple[float, float]]:
    """Find excessive gaps between fully timestamped words.

    The cut intervals are absolute source-audio times. We fail safely and return
    no cuts when lexical word timestamps do not cover the full transcript. A
    clause or sentence boundary keeps more silence than an ordinary word gap.
    """
    threshold = float(threshold_seconds)
    if threshold <= 0 or trim_end <= trim_start:
        return []
    if not isinstance(transcription, dict):
        return []

    entries: list[dict[str, Any]] = []
    raw_segments = transcription.get("segments") or []
    for segment_index, segment in enumerate(raw_segments):
        if not isinstance(segment, dict):
            continue
        raw_words = segment.get("words") or []
        for word_index, word in enumerate(raw_words):
            if not isinstance(word, dict):
                continue
            token = str(word.get("word") or word.get("text") or "").strip()
            if count_words(token) == 0:
                continue
            start, end = word.get("start"), word.get("end")
            if not (_is_finite_number(start) and _is_finite_number(end)):
                continue
            start_f, end_f = float(start), float(end)
            if start_f < 0 or end_f <= start_f:
                continue
            entries.append({
                "start": start_f,
                "end": end_f,
                "token": token,
                "segment_index": segment_index,
                "word_index": word_index,
            })

    entries.sort(key=lambda item: (item["start"], item["end"]))
    if not entries or not _timed_words_match_transcript(transcription, entries):
        return []

    intervals: list[tuple[float, float]] = []
    for previous, following in zip(entries, entries[1:]):
        gap = float(following["start"]) - float(previous["end"])
        if gap <= threshold:
            continue

        keep = max(0.0, float(keep_gap_seconds))
        previous_segment = int(previous["segment_index"])
        following_segment = int(following["segment_index"])
        if previous_segment != following_segment:
            keep = max(keep, float(clause_keep_seconds))

        punctuation = str(previous["token"])[-1:] if previous["token"] else ""
        if previous_segment == following_segment:
            segment = raw_segments[previous_segment]
            raw_words = segment.get("words") or []
            between = raw_words[int(previous["word_index"]) + 1:int(following["word_index"])]
            punctuation_between = "".join(
                str(item.get("word") or item.get("text") or "").strip()
                for item in between if isinstance(item, dict)
            )
            punctuation += punctuation_between

        if previous_segment != following_segment:
            segment_text = str(raw_segments[previous_segment].get("text") or "").rstrip()
            if segment_text and segment_text[-1:] in ".!?…":
                punctuation += segment_text[-1:]
        if any(mark in punctuation for mark in (".", "!", "?", "…")):
            keep = max(keep, float(sentence_keep_seconds))
        elif any(mark in punctuation for mark in (",", ";", ":", "—")):
            keep = max(keep, float(clause_keep_seconds))

        start = max(float(trim_start), float(previous["end"]) + keep)
        end = min(float(trim_end), float(following["start"]))
        if end - start > 0.035:
            intervals.append((start, end))

    if not intervals:
        return []

    # Merge any overlapping cuts caused by imperfect ASR word alignments.
    merged: list[list[float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + 0.005:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(round(start, 6), round(end, 6)) for start, end in merged]


def _pause_select_filter(
    removed_intervals: list[tuple[float, float]], trim_start: float
) -> str:
    """Build a compact FFmpeg aselect expression for intervals in trimmed time."""
    terms = []
    for start, end in removed_intervals:
        relative_start = max(0.0, float(start) - float(trim_start))
        relative_end = max(relative_start, float(end) - float(trim_start))
        if relative_end - relative_start > 0.01:
            terms.append(
                f"between(t,{relative_start:.6f},{relative_end:.6f})"
            )
    if not terms:
        return ""
    # FFmpeg evaluates this expression on audio frames. asetpts=N/SR/TB directly
    # after it rebuilds a continuous time base without the removed gaps.
    return "aselect='not(" + "+".join(terms) + ")'"


def count_timed_words(words: list[dict[str, Any]]) -> int:
    """Count lexical words in timed tokens, склеивая дефисные хвосты Whisper.

    Whisper отдаёт «что-то» как «Что» + «-то»; по отдельности это 2 слова,
    вместе — одно (как count_words). Иначе WPM завышается на дефисных словах.
    """
    total = 0
    pending = ""
    for word in words:
        token = str(word.get("token") or word.get("word") or word.get("text") or "").strip()
        if not token:
            continue
        if token.startswith("-") and pending:
            pending += token
            continue
        total += count_words(pending)
        pending = token
    total += count_words(pending)
    return total


def count_words_in_window(
    transcription: dict[str, Any] | None,
    start_seconds: float,
    end_seconds: float,
) -> int:
    """Count lexical words that belong to the retained audio window.

    If word timestamps are available, they are authoritative. If they are not
    available at all, fall back to the full transcript because trim bounds will
    also fail safe to the full source file.
    """
    words = timed_words(transcription)
    if not words or not _timed_words_match_transcript(transcription, words):
        return count_words(transcription_text(transcription))
    start = float(start_seconds)
    end = float(end_seconds)
    return count_timed_words([
        word for word in words
        if float(word["end"]) > start and float(word["start"]) < end
    ])


def effective_wpm(word_count: int, duration_seconds: float) -> float:
    """Words per minute over the complete narration duration."""
    if word_count <= 0 or not _is_finite_number(duration_seconds):
        return 0.0
    duration = float(duration_seconds)
    if duration <= 0:
        return 0.0
    return float(word_count) * 60.0 / duration


def normalize_speed_mode(value: Any) -> str:
    """Канонический режим скорости: 'coefficient' | 'wpm'.

    Принимает и русские подписи из GUI («По коэффициенту»), и канонические значения.
    """
    text = str(value or "").strip().lower()
    if text in ("coefficient", "коэффициент", "по коэффициенту"):
        return SPEED_MODE_COEFFICIENT
    return SPEED_MODE_WPM


def speed_mode_label(value: Any) -> str:
    """Подпись режима скорости для GUI-комбобокса."""
    return SPEED_MODE_LABELS[normalize_speed_mode(value)]


def normalize_short_speed_mode(value: Any) -> str:
    """Канонический режим скорости шортов: 'inherit' | 'wpm' | 'coefficient'.

    Принимает русские подписи из GUI и канонические значения. Неизвестное
    значение = 'inherit' (безопасный no-op: шорт идёт как длинный).
    """
    text = str(value or "").strip().lower()
    if text in ("coefficient", "коэффициент", "по коэффициенту"):
        return SPEED_MODE_COEFFICIENT
    if text in ("wpm", "слова/мин", "слов/мин", "по словам/мин"):
        return SPEED_MODE_WPM
    return SPEED_MODE_INHERIT


def short_speed_mode_label(value: Any) -> str:
    """Подпись режима скорости шортов для GUI-комбобокса."""
    return SHORT_SPEED_MODE_LABELS[normalize_short_speed_mode(value)]


def short_speed_ratio(
    mode: Any,
    target_wpm: Any = DEFAULT_TARGET_WPM,
    coefficient: Any = 1.0,
    *,
    metrics: dict[str, Any] | None = None,
    measured_wpm: float | None = None,
) -> float:
    """Во сколько раз ускорить/замедлить шорт относительно готового длинного.

    'inherit' → 1.0 (шорт как длинный). 'wpm' → target / фактический wpm
    длинного (final_wpm из метрик; при выключенном выравнивании — measured_wpm).
    'coefficient' → целевой множитель / фактический множитель длинного.
    Результат ограничен [0.5, 2.0]; разница меньше 1% считается no-op (1.0).
    """
    normalized = normalize_short_speed_mode(mode)
    if normalized == SPEED_MODE_INHERIT:
        return 1.0
    data = metrics or {}
    if normalized == SPEED_MODE_COEFFICIENT:
        long_factor = _safe_float(data.get("tempo_factor", 1.0), 1.0)
        if long_factor <= 0:
            long_factor = 1.0
        ratio = _safe_float(coefficient, 1.0) / long_factor
    else:
        current = data.get("final_wpm")
        if not _is_finite_number(current):
            current = measured_wpm
        current = _safe_float(current, 0.0)
        if current <= 0:
            return 1.0
        ratio = _safe_float(target_wpm, DEFAULT_TARGET_WPM) / current
    ratio = min(2.0, max(0.5, ratio))
    if abs(ratio - 1.0) < 0.01:
        return 1.0
    return ratio


def map_intervals_to_window(
    intervals: list[tuple[float, float]], window_start: float, window_end: float
) -> list[tuple[float, float]]:
    """Интервалы, переведённые в локальное окно [window_start, window_end].

    Всё за пределами окна отбрасывается, края обрезаются по границам окна.
    """
    lo = float(window_start)
    hi = float(window_end)
    result: list[tuple[float, float]] = []
    for raw_start, raw_end in intervals or []:
        start = max(lo, float(raw_start))
        end = min(hi, float(raw_end))
        if end - start > 0.02:
            result.append((start - lo, end - lo))
    return result


def scale_after_removals(
    value: Any, intervals: list[tuple[float, float]], ratio: Any = 1.0
) -> float:
    """Время на таймлайне шорта: сдвиг после вырезанных интервалов, затем скорость."""
    speed = _safe_float(ratio, 1.0)
    if speed <= 0:
        speed = 1.0
    return shift_time_after_removals(value, intervals) / speed


def adjust_short_clip_times(
    clip: dict[str, Any],
    *,
    window_start: float,
    intervals: list[tuple[float, float]],
    ratio: Any = 1.0,
) -> dict[str, Any]:
    """Тайминги клипа (long-таймлайн) → таймлайн обработанного шорта.

    ``intervals`` — уже локальные (от начала окна шорта, как их выдаёт
    ``map_intervals_to_window``).
    """
    out = dict(clip)
    for key in ("start", "end", "hook_start", "hook_end", "cta_start", "cta_end"):
        if key in out and out[key] is not None:
            local = _safe_float(out[key], 0.0) - float(window_start)
            out[key] = scale_after_removals(local, intervals, ratio)
    return out


def plan_short_adjustments(
    ctx: Any, *, window_start: float, window_end: float
) -> dict[str, Any]:
    """Что сделать с готовым шортом: скорость (ratio) и вырезания (локальные интервалы).

    Учитывает отдельные настройки шортов: режим скорости («как на длинном» — no-op)
    и галку вырезки. Если длинный уже вырезал повторы — шорт их наследует, ничего
    дополнительно не режем.
    """
    metrics = getattr(ctx, "speech_metrics", None)
    metrics = metrics if isinstance(metrics, dict) else {}
    mode = getattr(ctx, "s_speech_rate_mode", SPEED_MODE_INHERIT)
    measured_wpm: float | None = None
    if normalize_short_speed_mode(mode) != SPEED_MODE_INHERIT and not _is_finite_number(
        metrics.get("final_wpm")
    ):
        duration = _safe_float(getattr(ctx, "audio_duration", 0.0), 0.0)
        if duration > 0:
            word_count = count_words_in_window(
                getattr(ctx, "transcription", None), 0.0, duration
            )
            if word_count > 0:
                measured_wpm = effective_wpm(word_count, duration)
    ratio = short_speed_ratio(
        mode,
        getattr(ctx, "s_speech_rate_target_wpm", DEFAULT_TARGET_WPM),
        getattr(ctx, "s_speech_rate_coefficient", 1.0),
        metrics=metrics,
        measured_wpm=measured_wpm,
    )
    intervals: list[tuple[float, float]] = []
    if bool(getattr(ctx, "s_cut_extra_words", False)) and not metrics.get(
        "extra_words_applied"
    ):
        analysis = getattr(ctx, "analysis", None)
        corrected = ""
        if isinstance(analysis, dict):
            corrected = str(analysis.get("corrected_text") or "")
        if corrected:
            raw = find_extra_word_intervals(
                getattr(ctx, "transcription", None), corrected
            )
            intervals = map_intervals_to_window(raw, window_start, window_end)
    return {"ratio": ratio, "intervals": intervals}


def _safe_float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def speed_params_from_settings(
    mode: Any,
    target_wpm: Any = DEFAULT_TARGET_WPM,
    coefficient: Any = 1.0,
) -> dict[str, float]:
    """Параметры prepare_narration из настроек GUI (ползунок скорости).

    wpm: цель = ползунок, полоса без изменений [target, target+30].
    coefficient: явный множитель темпа (итог = исходный темп × коэффициент).
    """
    if normalize_speed_mode(mode) == SPEED_MODE_COEFFICIENT:
        k = _safe_float(coefficient, 1.0)
        return {"tempo_coefficient": min(2.0, max(0.5, k))}
    target = min(300.0, max(60.0, _safe_float(target_wpm, DEFAULT_TARGET_WPM)))
    return {"min_wpm": target, "max_wpm": target + 30.0, "target_wpm": target}


def load_speech_state() -> dict[str, Any]:
    """Последние метрики подготовки речи (подсказка ползунка в GUI)."""
    try:
        if os.path.isfile(SPEECH_STATE_PATH):
            with open(SPEECH_STATE_PATH, "r", encoding="utf-8") as stream:
                data = json.load(stream)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_speech_state(metrics: dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(SPEECH_STATE_PATH) or ".", exist_ok=True)
        payload = {
            key: metrics.get(key)
            for key in (
                "word_count", "measured_wpm_before_adjustment", "final_wpm",
                "tempo_factor", "pause_seconds_removed", "speed_mode", "status",
            )
        }
        payload["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(SPEECH_STATE_PATH, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=1)
    except Exception:
        pass


def find_trim_bounds(
    transcription: dict[str, Any] | None,
    source_duration: float,
    *,
    lead_pad_seconds: float = DEFAULT_LEAD_PAD_SECONDS,
    tail_pad_seconds: float = DEFAULT_TAIL_PAD_SECONDS,
) -> tuple[float, float, str]:
    """Find outer trim points from the first/last aligned spoken words.

    Small guards remain before the first word and after the last word to avoid
    chopping a plosive, breath, or the final consonant. Internal pauses are kept.
    Returns (start, end, method); if timing is unavailable it returns the whole
    file and ``no_word_timestamps`` rather than guessing.
    """
    try:
        duration = float(source_duration)
    except (TypeError, ValueError):
        return 0.0, 0.0, "invalid_duration"
    if not math.isfinite(duration) or duration <= 0:
        return 0.0, 0.0, "invalid_duration"

    words = timed_words(transcription)
    if not words:
        return 0.0, duration, "no_word_timestamps"
    if not _timed_words_match_transcript(transcription, words):
        return 0.0, duration, "incomplete_word_timestamps"

    first_start = min(float(w["start"]) for w in words)
    last_end = max(float(w["end"]) for w in words)
    start = max(0.0, first_start - max(0.0, float(lead_pad_seconds)))
    end = min(duration, last_end + max(0.0, float(tail_pad_seconds)))

    # Reject implausible timings instead of creating a nearly empty audio file.
    if end - start < min(0.25, duration * 0.5):
        return 0.0, duration, "invalid_word_bounds"
    return start, end, "word_timestamps"


def plan_tempo_factor(
    current_wpm: float,
    *,
    min_wpm: float = DEFAULT_MIN_WPM,
    max_wpm: float = DEFAULT_MAX_WPM,
    target_wpm: float = DEFAULT_TARGET_WPM,
) -> float:
    """Return FFmpeg tempo multiplier; values above 1 speed up speech.

    Do not alter audio already in the desired 120–150 WPM band. If outside the
    band, move toward the lower limit (120 WPM by default). ``atempo`` changes the
    playback tempo without the pitch shift caused by ``asetrate``.
    """
    low, high, target, current = map(float, (min_wpm, max_wpm, target_wpm, current_wpm))
    if low <= 0 or high < low:
        raise ValueError("WPM band must satisfy 0 < min_wpm <= max_wpm")
    if not low <= target <= high:
        raise ValueError("target_wpm must be inside the WPM band")
    if not math.isfinite(current) or current <= 0:
        return 1.0
    if low <= current <= high:
        return 1.0
    return target / current


def atempo_chain(tempo_factor: float) -> list[float]:
    """Split a tempo factor into safe atempo stages whose product is the target.

    FFmpeg atempo accepts [0.5, 100], but factors above 2 can skip samples. This
    helper keeps each stage in [0.5, 2.0] to avoid that high-speed shortcut.
    """
    factor = float(tempo_factor)
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("tempo_factor must be a finite positive number")
    chain: list[float] = []
    while factor > 2.0 + 1e-12:
        chain.append(2.0)
        factor /= 2.0
    while factor < 0.5 - 1e-12:
        chain.append(0.5)
        factor /= 0.5
    chain.append(factor)
    return chain


def _atempo_filter(tempo_factor: float) -> str:
    return ",".join(f"atempo={value:.8f}" for value in atempo_chain(tempo_factor))


def _rubberband_available(ffmpeg_bin: str) -> bool:
    cached = _RUBBERBAND_CACHE.get(ffmpeg_bin)
    if cached is not None:
        return cached
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        listing = (result.stdout or "") + "\n" + (result.stderr or "")
        available = result.returncode == 0 and re.search(r"\brubberband\b", listing) is not None
    except (OSError, subprocess.SubprocessError):
        available = False
    _RUBBERBAND_CACHE[ffmpeg_bin] = available
    return available


def _probe_duration(path: str, ffprobe_bin: str = "ffprobe") -> float:
    result = subprocess.run(
        [
            ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {(result.stderr or '')[-500:]}")
    try:
        duration = float((result.stdout or "").strip())
    except ValueError as exc:
        raise RuntimeError(f"ffprobe returned invalid duration for {path}") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError(f"ffprobe returned non-positive duration for {path}: {duration}")
    return duration


def _transformed_time(
    value: Any,
    trim_start: float,
    tempo_factor: float,
    final_duration: float,
    removed_intervals: list[tuple[float, float]] | None = None,
) -> float:
    if not _is_finite_number(value):
        return 0.0
    source_time = max(float(trim_start), float(value))
    removed_before = 0.0
    for gap_start, gap_end in removed_intervals or []:
        gap_start, gap_end = float(gap_start), float(gap_end)
        if source_time >= gap_end:
            removed_before += gap_end - gap_start
        elif source_time >= gap_start:
            # A boundary/event inside removed silence is anchored to the cut start.
            source_time = gap_start
            break
        else:
            break
    new_value = (source_time - trim_start - removed_before) / tempo_factor
    return round(max(0.0, min(final_duration, new_value)), 6)


def retime_transcription(
    transcription: dict[str, Any],
    *,
    trim_start: float,
    trim_end: float,
    tempo_factor: float,
    final_duration: float,
    removed_intervals: list[tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """Shift/scale segment and word timestamps to match the prepared audio file."""
    out = copy.deepcopy(transcription)
    new_segments: list[dict[str, Any]] = []
    for segment in out.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        original_words = segment.get("words") or []
        transformed_words: list[dict[str, Any]] = []
        invalid_word_timing = bool(original_words) and any(
            not isinstance(word, dict)
            or not (_is_finite_number(word.get("start")) and _is_finite_number(word.get("end")))
            or float(word.get("end", 0) or 0) <= float(word.get("start", 0) or 0)
            for word in original_words
        )
        # Never drop a whole subtitle segment merely because part of its word-level
        # timestamps is missing. Fall back to segment timing and remove unreliable
        # word events so downstream code can use the intact segment text.
        if invalid_word_timing:
            seg_start, seg_end = segment.get("start"), segment.get("end")
            if _is_finite_number(seg_start) and _is_finite_number(seg_end) and float(seg_end) > float(seg_start):
                clipped_start, clipped_end = max(float(seg_start), trim_start), min(float(seg_end), trim_end)
                if clipped_end <= clipped_start:
                    continue
                segment["start"] = _transformed_time(clipped_start, trim_start, tempo_factor, final_duration, removed_intervals)
                segment["end"] = _transformed_time(clipped_end, trim_start, tempo_factor, final_duration, removed_intervals)
            segment.pop("words", None)
            new_segments.append(segment)
            continue
        for word in original_words:
            if not isinstance(word, dict):
                continue
            token = str(word.get("word") or word.get("text") or "").strip()
            # Punctuation never contributes to the WPM count, but preserve a
            # separately timed punctuation token for subtitle engines that use
            # word-level events. Punctuation attached to lexical tokens remains
            # untouched as part of that token's display text.
            if not (_is_finite_number(word.get("start")) and _is_finite_number(word.get("end"))):
                continue
            old_start, old_end = float(word["start"]), float(word["end"])
            clipped_start, clipped_end = max(old_start, trim_start), min(old_end, trim_end)
            if clipped_end <= clipped_start:
                continue
            new_word = dict(word)
            inside_removed_gap = any(
                clipped_start < gap_end and clipped_end > gap_start
                for gap_start, gap_end in (removed_intervals or [])
            )
            if inside_removed_gap and count_words(token) == 0:
                # Preserve standalone punctuation events even if an imperfect ASR
                # timestamp places them in the silence portion being shortened.
                gap_start = next(
                    float(gap_start) for gap_start, gap_end in (removed_intervals or [])
                    if clipped_start < float(gap_end) and clipped_end > float(gap_start)
                )
                anchor = _transformed_time(gap_start, trim_start, tempo_factor, final_duration, removed_intervals)
                new_word["start"] = anchor
                new_word["end"] = min(final_duration, anchor + 0.01)
                transformed_words.append(new_word)
                continue
            new_word["start"] = _transformed_time(clipped_start, trim_start, tempo_factor, final_duration, removed_intervals)
            new_word["end"] = _transformed_time(clipped_end, trim_start, tempo_factor, final_duration, removed_intervals)
            if new_word["end"] > new_word["start"]:
                transformed_words.append(new_word)

        if original_words:
            if not transformed_words:
                continue
            segment["words"] = transformed_words
            segment["start"] = min(w["start"] for w in transformed_words)
            segment["end"] = max(w["end"] for w in transformed_words)
        else:
            start = float(segment.get("start", 0) or 0)
            end = float(segment.get("end", 0) or 0)
            clipped_start, clipped_end = max(start, trim_start), min(end, trim_end)
            if clipped_end <= clipped_start:
                continue
            segment["start"] = _transformed_time(clipped_start, trim_start, tempo_factor, final_duration, removed_intervals)
            segment["end"] = _transformed_time(clipped_end, trim_start, tempo_factor, final_duration, removed_intervals)
        new_segments.append(segment)
    out["segments"] = new_segments
    out["duration"] = round(float(final_duration), 6)
    return out


def prepared_narration_path(source_path: str, output_dir: str) -> str:
    """Build a stable, per-source temporary path to avoid batch-file collisions."""
    absolute = os.path.abspath(source_path)
    try:
        stat = os.stat(absolute)
        fingerprint = f"{absolute}|{stat.st_size}|{stat.st_mtime_ns}"
    except OSError:
        fingerprint = absolute
    suffix = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:12]
    return os.path.join(output_dir, f"narration_prepared_{suffix}.wav")


def prepare_narration(
    source_path: str,
    output_path: str,
    transcription: dict[str, Any],
    *,
    min_wpm: float = DEFAULT_MIN_WPM,
    max_wpm: float = DEFAULT_MAX_WPM,
    target_wpm: float = DEFAULT_TARGET_WPM,
    tempo_coefficient: float | None = None,
    lead_pad_seconds: float = DEFAULT_LEAD_PAD_SECONDS,
    tail_pad_seconds: float = DEFAULT_TAIL_PAD_SECONDS,
    remove_long_pauses: bool = False,
    pause_threshold_seconds: float = DEFAULT_PAUSE_THRESHOLD_SECONDS,
    pause_keep_seconds: float = DEFAULT_PAUSE_KEEP_SECONDS,
    clause_pause_keep_seconds: float = DEFAULT_CLAUSE_PAUSE_KEEP_SECONDS,
    sentence_pause_keep_seconds: float = DEFAULT_SENTENCE_PAUSE_KEEP_SECONDS,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    log_fn: LogFn | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Trim outer silence, optionally shorten long pauses, and adjust WPM.

    Returns ``(audio_path_for_render, retimed_transcription, metrics)``. When no
    processing is needed, it returns the original path. If processing is needed
    but FFmpeg fails, it raises instead of silently rendering a wrong timeline.
    Pause compression defaults off and only uses complete word-level timestamps.
    """
    _log = log_fn or (lambda _message: None)
    resolved_source = resolve_existing_file(source_path)
    if not resolved_source:
        raise FileNotFoundError(f"Narration source does not exist: {source_path}")
    if resolved_source != source_path:
        _log(f"[SPEECH RATE] путь narration нормализован: {resolved_source}")
    source_path = resolved_source

    duration = _probe_duration(source_path, ffprobe_bin=ffprobe_bin)
    text = transcription_text(transcription)
    trim_start, trim_end, trim_method = find_trim_bounds(
        transcription,
        duration,
        lead_pad_seconds=lead_pad_seconds,
        tail_pad_seconds=tail_pad_seconds,
    )
    words_count = count_words_in_window(transcription, trim_start, trim_end)
    trimmed_duration = max(0.0, trim_end - trim_start)
    raw_measured_wpm = effective_wpm(words_count, trimmed_duration)
    removed_intervals: list[tuple[float, float]] = []
    if remove_long_pauses:
        removed_intervals = find_long_pause_intervals(
            transcription,
            trim_start,
            trim_end,
            threshold_seconds=pause_threshold_seconds,
            keep_gap_seconds=pause_keep_seconds,
            clause_keep_seconds=clause_pause_keep_seconds,
            sentence_keep_seconds=sentence_pause_keep_seconds,
        )
        if not removed_intervals:
            timed = timed_words(transcription)
            if not timed or not _timed_words_match_transcript(transcription, timed):
                _log(
                    "[PAUSE REDUCTION] пропуск: нет полного набора таймкодов всех слов; "
                    "аудио не будет обрезаться наугад."
                )
            else:
                _log("[PAUSE REDUCTION] длинных межсловных пауз выше порога не найдено.")
    removed_pause_seconds = sum(end - start for start, end in removed_intervals)
    post_pause_duration = max(0.0, trimmed_duration - removed_pause_seconds)
    measured_wpm = effective_wpm(words_count, post_pause_duration)
    explicit_coefficient: float | None = None
    if tempo_coefficient is not None:
        explicit_coefficient = min(2.0, max(0.5, _safe_float(tempo_coefficient, 1.0)))
    if explicit_coefficient is not None:
        tempo_factor = explicit_coefficient
    else:
        tempo_factor = plan_tempo_factor(
            measured_wpm, min_wpm=min_wpm, max_wpm=max_wpm, target_wpm=target_wpm
        )
    wants_trim = trim_start > 0.01 or (duration - trim_end) > 0.01
    wants_pause_reduction = bool(removed_intervals)
    wants_tempo = abs(tempo_factor - 1.0) > 0.005
    metrics: dict[str, Any] = {
        "word_count": words_count,
        "source_duration_seconds": round(duration, 4),
        "trim_start_seconds": round(trim_start, 4),
        "trim_end_seconds": round(trim_end, 4),
        "trimmed_duration_seconds": round(trimmed_duration, 4),
        "trim_method": trim_method,
        "pause_reduction_enabled": bool(remove_long_pauses),
        "pause_threshold_seconds": float(pause_threshold_seconds),
        "pause_intervals_removed": len(removed_intervals),
        "pause_seconds_removed": round(removed_pause_seconds, 4),
        "measured_wpm_before_pause_reduction": round(raw_measured_wpm, 2),
        "measured_wpm_before_adjustment": round(measured_wpm, 2),
        "target_min_wpm": float(min_wpm),
        "target_max_wpm": float(max_wpm),
        "target_wpm": float(target_wpm),
        "tempo_factor": round(tempo_factor, 6),
        "speed_mode": SPEED_MODE_COEFFICIENT if explicit_coefficient is not None else SPEED_MODE_WPM,
        "tempo_changed": bool(wants_tempo),
        "trim_changed": bool(wants_trim),
        "tempo_method": "none",
        "final_duration_seconds": round(post_pause_duration, 4),
        "final_wpm": round(effective_wpm(words_count, post_pause_duration), 2),
        "status": "unchanged",
    }

    if words_count <= 0 or trimmed_duration <= 0:
        metrics["status"] = "skipped_no_valid_transcript"
        _log("[SPEECH RATE] Нет валидных слов/длительности — аудио не изменено.")
        return source_path, copy.deepcopy(transcription), metrics

    if not wants_trim and not wants_tempo and not wants_pause_reduction:
        metrics["status"] = "already_in_range" if min_wpm <= measured_wpm <= max_wpm else "unchanged"
        _log(
            f"[SPEECH RATE] {words_count} слов / {trimmed_duration:.2f} c = {measured_wpm:.1f} WPM; "
            f"паузы удалены: 0 c; скорость в диапазоне {min_wpm:.0f}–{max_wpm:.0f}, аудио не меняем."
        )
        _save_speech_state(metrics)
        return source_path, copy.deepcopy(transcription), metrics

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    if os.path.abspath(output_path) == os.path.abspath(source_path):
        raise ValueError("output_path must differ from source_path")

    filters = [f"atrim=start={trim_start:.6f}:end={trim_end:.6f}", "asetpts=PTS-STARTPTS"]
    pause_filter = _pause_select_filter(removed_intervals, trim_start)
    if pause_filter:
        filters.extend([pause_filter, "asetpts=N/SR/TB"])
    if wants_tempo:
        if _rubberband_available(ffmpeg_bin):
            filters.append(f"rubberband=tempo={tempo_factor:.8f}:pitch=1.0:formant=preserved")
            metrics["tempo_method"] = "rubberband_formant_preserved"
        else:
            filters.extend(_atempo_filter(tempo_factor).split(","))
            metrics["tempo_method"] = "atempo_pitch_preserving"
    expected_duration = post_pause_duration / tempo_factor
    # Tiny fades remove edit-boundary clicks without changing the voice character.
    if expected_duration >= 0.08:
        filters.append("afade=t=in:st=0:d=0.015")
        fade_out_duration = 0.025
        filters.append(f"afade=t=out:st={max(0.0, expected_duration - fade_out_duration):.6f}:d={fade_out_duration:.3f}")

    command = [
        ffmpeg_bin, "-hide_banner", "-y", "-i", source_path,
        "-map", "0:a:0", "-vn", "-af", ",".join(filters),
        "-c:a", "pcm_s16le", "-ar", "48000", "-map_metadata", "-1", output_path,
    ]
    _log(
        f"[SPEECH RATE] слов={words_count}; темп до пауз={raw_measured_wpm:.1f} WPM; "
        f"темп после пауз={measured_wpm:.1f} WPM; удалено пауз={removed_pause_seconds:.2f}s "
        f"({len(removed_intervals)} интервалов); "
        f"trim={trim_start:.3f}..{trim_end:.3f}s; tempo={tempo_factor:.4f} "
        f"({'ускорение' if tempo_factor > 1 else 'замедление' if tempo_factor < 1 else 'без изменения'})."
    )
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3600, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("FFmpeg narration preparation timed out after 3600s") from exc
    if result.returncode != 0 or not os.path.isfile(output_path) or os.path.getsize(output_path) < 1024:
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except OSError:
            pass
        detail = (result.stderr or result.stdout or "FFmpeg failed")[-1200:]
        raise RuntimeError(f"Narration preparation failed; refusing to silently use unprocessed audio: {detail}")

    final_duration = _probe_duration(output_path, ffprobe_bin=ffprobe_bin)
    # Use the measured output duration to retime word timestamps. Rubber Band and
    # atempo may differ by a few milliseconds from the theoretical factor; using
    # the measured ratio keeps subtitles aligned with the actual rendered audio.
    actual_tempo_factor = post_pause_duration / final_duration if final_duration > 0 else tempo_factor
    transformed = retime_transcription(
        transcription,
        trim_start=trim_start,
        trim_end=trim_end,
        tempo_factor=actual_tempo_factor,
        final_duration=final_duration,
        removed_intervals=removed_intervals,
    )
    metrics.update({
        "final_duration_seconds": round(final_duration, 4),
        "actual_tempo_factor": round(actual_tempo_factor, 8),
        "final_wpm": round(effective_wpm(words_count, final_duration), 2),
        "pause_reduction_applied": bool(removed_intervals),
        "status": "processed",
        "output_path": output_path,
        "used_rubberband": metrics["tempo_method"] == "rubberband_formant_preserved",
    })
    _log(
        f"[SPEECH RATE] итог: {words_count} слов / {final_duration:.2f} c = "
        f"{metrics['final_wpm']:.1f} WPM; метод={metrics['tempo_method']}; "
        f"тембр/высота сохраняются time-stretch фильтром."
    )
    if tempo_factor > 1.55 or tempo_factor < 0.65:
        _log("[SPEECH RATE][WARNING] Требуется сильная коррекция темпа; перед публикацией прослушайте голос.")
    _save_speech_state(metrics)
    return output_path, transformed, metrics
